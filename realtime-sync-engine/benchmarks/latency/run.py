#!/usr/bin/env python
"""Reproducible broadcast-latency benchmark for the Real-Time Sync Engine.

Broadcast latency = time from the sender writing a mutation to its socket until a
*different* subscribed client receives the matching `update`. That covers the
whole path: client -> Channels -> version check -> PostgreSQL commit -> Redis
PUBLISH -> Channels (other process) -> client.

By default the sender talks to server A and every receiver to server B, so each
measured message must cross Redis between two OS processes.

Examples
    python -m latency.run                                   # standard suite
    python -m latency.run --scenario fanout --receivers 1,10,50 --count 3000
    python -m latency.run --scenario contention --writers 8
    python -m latency.run --server-b http://localhost:8000  # single-process run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .clients import Client, create_document, ensure_user, http, ws_url
from .stats import histogram, summarize_ns

RESULTS_DIR = Path(__file__).parent / "results"


# --------------------------------------------------------------------------- helpers


def _ops_for(i: int) -> list[dict]:
    # Alternate insert/delete so the document stays tiny and every op is valid.
    if i % 2 == 0:
        return [{"kind": "insert", "pos": 0, "text": "x"}]
    return [{"kind": "delete", "pos": 0, "length": 1}]


async def _start_all(clients: list[Client], batch: int = 25) -> None:
    for i in range(0, len(clients), batch):
        await asyncio.gather(*(c.start() for c in clients[i:i + batch]))
    await asyncio.sleep(0.5)  # let presence chatter from joins die down


def _metrics(server: str, token: str, reset: bool = False) -> dict | None:
    status, body = http(server, "/api/metrics/sync/", "DELETE" if reset else "GET", token=token)
    return body if status == 200 else None


async def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


# --------------------------------------------------------------------------- scenarios


async def scenario_fanout(args, token: str, receivers: int) -> dict:
    """One sender, N receivers, open-loop schedule at a fixed rate."""
    doc = create_document(args.server_a, token, f"bench fanout x{receivers}")
    sender = Client("sender", ws_url(args.server_a, doc, token), doc)
    rx = [Client(f"rx{i}", ws_url(args.server_b, doc, token), doc) for i in range(receivers)]
    await _start_all([sender, *rx])
    for server in {args.server_a, args.server_b}:
        _metrics(server, token, reset=True)

    period_ns = int(1e9 / args.rate)
    total = args.warmup + args.count
    sent: list[tuple[str, int, int]] = []   # (mutation_id, t_scheduled, t_sent)
    acks_ns: list[int] = []
    version = sender.version
    start = time.perf_counter_ns() + 50_000_000

    for i in range(total):
        scheduled = start + i * period_ns
        delay = (scheduled - time.perf_counter_ns()) / 1e9
        if delay > 0:
            await asyncio.sleep(delay)
        mutation_id, t_send, fut = await sender.send_mutation(version, _ops_for(i))
        reply = await asyncio.wait_for(fut, timeout=10)
        if reply["type"] != "ack":
            raise RuntimeError(f"unexpected reply in fanout (single writer): {reply}")
        version = reply["version"]
        if i >= args.warmup:
            sent.append((mutation_id, scheduled, t_send))
            acks_ns.append(time.perf_counter_ns() - t_send)

    last = sent[-1][0]
    await _wait_for(lambda: all(last in r.received for r in rx), timeout=10)

    broadcast, corrected, lost = [], [], 0
    for mutation_id, scheduled, t_send in sent:
        for r in rx:
            t_recv = r.received.get(mutation_id)
            if t_recv is None:
                lost += 1
                continue
            broadcast.append(t_recv - t_send)
            corrected.append(t_recv - min(scheduled, t_send))

    result = {
        "scenario": "fanout",
        "receivers": receivers,
        "mutations": args.count,
        "rate_per_s": args.rate,
        "broadcast_latency": summarize_ns(broadcast),
        "broadcast_latency_from_schedule": summarize_ns(corrected),
        "sender_ack_latency": summarize_ns(acks_ns),
        "histogram": histogram(broadcast),
        "lost_deliveries": lost,
        "server_stages": {s: _metrics(s, token) for s in {args.server_a, args.server_b}},
    }
    await asyncio.gather(*(c.stop() for c in [sender, *rx]))
    return result


async def scenario_contention(args, token: str, writers: int) -> dict:
    """W writers race on the same document; an observer on server B measures the
    broadcast latency of every accepted mutation. Reports conflict rate."""
    doc = create_document(args.server_a, token, f"bench contention x{writers}")
    servers = [args.server_a, args.server_b]
    ws = [Client(f"w{i}", ws_url(servers[i % 2], doc, token), doc) for i in range(writers)]
    observer = Client("observer", ws_url(args.server_b, doc, token), doc)
    await _start_all([*ws, observer])
    per_writer = max(1, args.count // writers)
    period = 1.0 / max(1e-9, args.rate / writers)
    accepted: list[tuple[str, int]] = []
    conflicts = 0

    async def run_writer(c: Client, offset: float):
        nonlocal conflicts
        await asyncio.sleep(offset)
        done = 0
        while done < per_writer:
            tick = time.monotonic()
            # Retry until accepted: a conflict means "fetch newest version, try again".
            while True:
                mutation_id, t_send, fut = await c.send_mutation(c.version, [{"kind": "insert", "pos": 0, "text": "w"}])
                reply = await asyncio.wait_for(fut, timeout=10)
                if reply["type"] == "ack":
                    accepted.append((mutation_id, t_send))
                    break
                if reply["type"] != "conflict":
                    raise RuntimeError(f"unexpected reply: {reply}")
                conflicts += 1
            done += 1
            await asyncio.sleep(max(0.0, period - (time.monotonic() - tick)))

    started = time.perf_counter()
    await asyncio.gather(*(run_writer(c, i * period / writers) for i, c in enumerate(ws)))
    elapsed = time.perf_counter() - started
    await _wait_for(lambda: all(m in observer.received for m, _ in accepted), timeout=10)

    # Writers on server B never echo to themselves; observer sees everyone else's.
    lat = [observer.received[m] - t for m, t in accepted if m in observer.received]
    result = {
        "scenario": "contention",
        "writers": writers,
        "accepted": len(accepted),
        "conflicts": conflicts,
        "conflict_rate": conflicts / max(1, conflicts + len(accepted)),
        "throughput_per_s": len(accepted) / elapsed,
        "broadcast_latency": summarize_ns(lat),
        "histogram": histogram(lat),
        "lost_deliveries": len(accepted) - len(lat),
    }
    await asyncio.gather(*(c.stop() for c in [*ws, observer]))
    return result


# --------------------------------------------------------------------------- reporting


def _metadata(args) -> dict:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=2).stdout.strip() or None
    except Exception:  # noqa: BLE001
        sha = None
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "server_a": args.server_a,
        "server_b": args.server_b,
        "warmup": args.warmup,
    }


def _row(label: str, s: dict) -> str:
    if not s.get("count"):
        return f"{label:<34} (no samples)"
    return (f"{label:<34} n={s['count']:>6}  mean={s['mean_ms']:6.2f}  p50={s['p50_ms']:6.2f}  "
            f"p95={s['p95_ms']:6.2f}  p99={s['p99_ms']:6.2f}  max={s['max_ms']:7.2f} ms")


def print_report(results: list[dict], threshold: float) -> bool:
    ok = True
    print("\nBroadcast latency (sender write -> other client receives update)")
    print("-" * 110)
    for r in results:
        if r["scenario"] == "fanout":
            label = f"fanout   1 -> {r['receivers']} receivers"
        else:
            label = f"contention {r['writers']} writers"
        print(_row(label, r["broadcast_latency"]))
        if r["scenario"] == "fanout":
            print(_row("   from scheduled send time", r["broadcast_latency_from_schedule"]))
            print(_row("   sender ack round-trip", r["sender_ack_latency"]))
        else:
            print(f"   conflicts={r['conflicts']} ({r['conflict_rate']:.1%})  "
                  f"throughput={r['throughput_per_s']:.0f} accepted/s")
        if r["lost_deliveries"]:
            print(f"   LOST deliveries: {r['lost_deliveries']}")
            ok = False
        p99 = r["broadcast_latency"].get("p99_ms", float("inf"))
        if p99 >= threshold:
            ok = False
    print("-" * 110)
    for r in results:
        stages = (r.get("server_stages") or {})
        for server, m in stages.items():
            if m and m.get("stages"):
                parts = ", ".join(f"{k} p50={v['p50_ms']:.2f}/p99={v['p99_ms']:.2f}"
                                  for k, v in m["stages"].items())
                print(f"server stages [{server}, fanout x{r['receivers']}]: {parts}")
    print(f"\nResult: {'PASS' if ok else 'FAIL'} (p99 < {threshold} ms, no lost deliveries)")
    return ok


def markdown_summary(meta: dict, results: list[dict], threshold: float, ok: bool) -> str:
    lines = [
        "# Broadcast latency results",
        "",
        f"- Run: {meta['timestamp']} (git {meta['git_sha'] or 'n/a'})",
        f"- Machine: {meta['platform']}, {meta['machine']}, Python {meta['python']}",
        f"- Servers: sender on {meta['server_a']}, receivers on {meta['server_b']}",
        f"- Target: p99 < {threshold} ms. Result: **{'PASS' if ok else 'FAIL'}**",
        "",
        "| scenario | samples | mean ms | p50 ms | p95 ms | p99 ms | max ms | lost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        label = (f"fan-out 1 -> {r['receivers']}" if r["scenario"] == "fanout"
                 else f"contention, {r['writers']} writers ({r['conflict_rate']:.0%} conflicts)")
        s = r["broadcast_latency"]
        if not s.get("count"):
            lines.append(f"| {label} | 0 | | | | | | {r['lost_deliveries']} |")
            continue
        lines.append(f"| {label} | {s['count']} | {s['mean_ms']:.2f} | {s['p50_ms']:.2f} | "
                     f"{s['p95_ms']:.2f} | {s['p99_ms']:.2f} | {s['max_ms']:.2f} | {r['lost_deliveries']} |")
    return "\n".join(lines) + "\n"


async def main_async(args) -> int:
    token = ensure_user(args.server_a, "bench")
    results = []
    if args.scenario in ("fanout", "all"):
        for n in [int(x) for x in args.receivers.split(",")]:
            print(f"running fanout with {n} receiver(s) ...", flush=True)
            results.append(await scenario_fanout(args, token, n))
    if args.scenario in ("contention", "all"):
        for w in [int(x) for x in args.writers.split(",")]:
            print(f"running contention with {w} writer(s) ...", flush=True)
            results.append(await scenario_contention(args, token, w))

    ok = print_report(results, args.threshold_ms)
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-{args.scenario}.json"
    meta = _metadata(args)
    out.write_text(json.dumps({"metadata": meta, "results": results}, indent=2))
    summary = markdown_summary(meta, results, args.threshold_ms, ok)
    (RESULTS_DIR / f"{stamp}-{args.scenario}.md").write_text(summary)
    (RESULTS_DIR / "latest.md").write_text(summary)
    print(f"Saved {out} and {RESULTS_DIR / 'latest.md'}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server-a", default="http://localhost:8000", help="sender's server")
    parser.add_argument("--server-b", default="http://localhost:8001", help="receivers' server")
    parser.add_argument("--scenario", choices=["fanout", "contention", "all"], default="all")
    parser.add_argument("--receivers", default="1,10,50", help="comma-separated receiver counts")
    parser.add_argument("--writers", default="4", help="comma-separated writer counts (contention)")
    parser.add_argument("--count", type=int, default=2000, help="measured mutations per scenario")
    parser.add_argument("--warmup", type=int, default=200, help="discarded mutations")
    parser.add_argument("--rate", type=float, default=100.0, help="mutations per second")
    parser.add_argument("--threshold-ms", type=float, default=40.0)
    args = parser.parse_args()
    try:
        import uvloop  # noqa: F401

        uvloop.install()
    except ImportError:
        pass
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
