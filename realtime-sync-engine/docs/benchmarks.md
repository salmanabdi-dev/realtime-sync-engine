# Broadcast-latency benchmark

## What is measured

**Broadcast latency**: from the moment the sender hands a mutation to its socket
until a *different* client, subscribed to the same document, receives the
matching `update`. This covers the whole accepted-change path:

```
client → WebSocket → Channels → validation → PostgreSQL commit → Redis PUBLISH
       → Channels (another process) → WebSocket → client
```

Target: **p99 < 40 ms** locally.

## Method

- **One clock.** The sender and all receivers run in one asyncio process and
  timestamp with `time.perf_counter_ns()`. No cross-machine clock sync is needed.
  Receivers take the timestamp before parsing the frame.
- **Two server processes.** The sender connects to server A (`:8000`), receivers
  to server B (`:8001`). Every measured message must cross Redis between two OS
  processes, which is the production topology.
- **Warm-up.** The first 200 mutations are discarded.
- **Enough samples.** 2,000 measured mutations per scenario by default, so p99 is
  backed by ~20 samples per receiver.
- **Open-loop schedule.** Mutations are scheduled at a fixed rate (default
  100/s). Latency is also reported from the *scheduled* send time, so a server
  stall shows up in the numbers instead of silently pausing the benchmark
  (avoiding coordinated omission). The pass/fail check uses p99 of the
  sender-write measurement; the from-schedule number is printed alongside.
- **Production-like settings.** `config.settings.bench`: `DEBUG=False` (DEBUG
  records every SQL query), quiet logging. Durability (`fsync`,
  `synchronous_commit`) is left at PostgreSQL defaults on purpose.
- **Lost deliveries** (an accepted mutation that never reached a receiver) fail
  the run.

### Scenarios

| scenario | flags | what it shows |
|---|---|---|
| fan-out 1 → N | `--scenario fanout --receivers 1,10,50` | baseline and the cost of fan-out |
| contention | `--scenario contention --writers 4` | several writers racing; conflict rate and latency of accepted edits |

Each run prints mean / p50 / p95 / p99 / max, the sender's ack round trip, a
latency histogram, and per-stage server timings (`parse`, `commit`, `publish`,
`handle_total`) fetched from `/api/metrics/sync/` on both servers. Results are
saved as JSON in `benchmarks/latency/results/` with machine metadata and git SHA.

## Running it

```bash
make up            # once
make bench         # restarts both servers with bench settings, runs the suite
make bench BENCH_ARGS="--scenario fanout --receivers 1,100 --count 5000"
```

From the host instead of a container:

```bash
pip install -r benchmarks/requirements.txt
make bench-local BENCH_ARGS="--server-a http://localhost:8000 --server-b http://localhost:8001"
```

Exit code is 0 on pass, 1 on fail, so the benchmark can gate CI.

## Reading the server-stage breakdown

| stage | includes |
|---|---|
| `parse` | JSON decode + validation |
| `commit` | thread-pool hop + the whole PostgreSQL transaction |
| `publish` | `group_send` to Redis |
| `handle_total` | receive → ack sent |

If `commit` dominates, look at PostgreSQL (disk fsync latency on your machine,
connection reuse). If p99 grows with receiver count while server stages stay
flat, the bottleneck is the benchmark process itself decoding frames for many
sockets on one core; split receivers across processes or compare p50.

## Tuning knobs

| knob | effect |
|---|---|
| `SYNC_DB_THREAD_SENSITIVE=0` | DB work for different sockets runs in parallel threads instead of one per process. Helps under many concurrent writers. Correctness doesn't depend on it. |
| `DB_POOL=1` | Django's psycopg connection pool instead of persistent per-thread connections. |
| orjson | already used for encoding; updates are encoded once per broadcast, not per receiver. |
| `--no-access-log` | already set; per-request logging costs real time at high rates. |

## Recording results

Paste the summary table from your run here, with the machine it ran on:

| date | machine | scenario | mean | p50 | p95 | p99 |
|---|---|---|---|---|---|---|
| | | | | | | |
