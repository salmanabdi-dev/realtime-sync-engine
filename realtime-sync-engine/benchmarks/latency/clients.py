"""Simulated WebSocket clients for the latency benchmark.

All clients run in one asyncio process and timestamp with the same monotonic
clock (time.perf_counter_ns), so latencies need no cross-machine clock sync.
Receivers take the timestamp *before* parsing the frame.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
import uuid

from websockets.asyncio.client import connect

try:
    import orjson

    _loads, _dumps = orjson.loads, lambda o: orjson.dumps(o).decode()
except ImportError:  # pragma: no cover
    _loads, _dumps = json.loads, json.dumps


# --------------------------------------------------------------------------- REST helpers


def http(base: str, path: str, method: str = "GET", data: dict | None = None, token: str | None = None):
    req = urllib.request.Request(f"{base}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Token {token}")
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(req, body, timeout=10) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else None)


def ensure_user(base: str, username: str, password: str = "bench-password") -> str:
    status, body = http(base, "/api/auth/login/", "POST", {"username": username, "password": password})
    if status == 200:
        return body["token"]
    status, body = http(base, "/api/auth/register/", "POST", {"username": username, "password": password})
    if status != 201:
        raise RuntimeError(f"could not create bench user {username}: {status} {body}")
    return body["token"]


def create_document(base: str, token: str, title: str) -> str:
    status, body = http(base, "/api/documents/", "POST", {"title": title, "content": ""}, token)
    if status != 201:
        raise RuntimeError(f"could not create document: {status} {body}")
    return body["id"]


def ws_url(http_base: str, document_id: str, token: str) -> str:
    base = http_base.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/ws/documents/{document_id}/?token={token}&client_id={uuid.uuid4()}"


# --------------------------------------------------------------------------- clients


class Client:
    """One WebSocket. Tracks version; resolves pending mutations on ack/conflict;
    timestamps every update by mutation_id."""

    def __init__(self, name: str, url: str, document_id: str):
        self.name = name
        self.url = url
        self.document_id = document_id
        self.client_id = str(uuid.uuid4())
        self.version: int | None = None
        self.ready = asyncio.Event()
        self.received: dict[str, int] = {}   # mutation_id -> perf_counter_ns at receipt
        self.updates = 0
        self.errors: list[dict] = []
        self._pending: dict[str, asyncio.Future] = {}
        self._ws = None
        self._reader: asyncio.Task | None = None

    async def start(self) -> None:
        # Disable compression and keepalive pings: they add CPU and jitter, not realism.
        self._ws = await connect(self.url, compression=None, ping_interval=None, max_size=2**22)
        self._reader = asyncio.create_task(self._read())
        await asyncio.wait_for(self.ready.wait(), timeout=10)

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()

    async def _read(self) -> None:
        async for frame in self._ws:
            t = time.perf_counter_ns()
            msg = _loads(frame)
            kind = msg["type"]
            if kind == "update":
                self.received[msg["mutation_id"]] = t
                self.updates += 1
                if self.version is None or msg["version"] > self.version:
                    self.version = msg["version"]
            elif kind in ("ack", "conflict", "error"):
                fut = self._pending.pop(msg.get("mutation_id", ""), None)
                if kind == "ack" and (self.version is None or msg["version"] > self.version):
                    self.version = msg["version"]
                elif kind == "conflict" and msg["current_version"] > (self.version or 0):
                    self.version = msg["current_version"]
                elif kind == "error":
                    self.errors.append(msg)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            elif kind == "snapshot":
                self.version = msg["version"]
                self.ready.set()
            elif kind == "catchup":
                self.version = msg["current_version"]
                self.ready.set()

    async def send_mutation(self, expected_version: int, ops: list[dict]) -> tuple[str, int, asyncio.Future]:
        mutation_id = str(uuid.uuid4())
        fut = asyncio.get_running_loop().create_future()
        self._pending[mutation_id] = fut
        frame = _dumps({
            "type": "mutation", "document_id": self.document_id, "expected_version": expected_version,
            "mutation_id": mutation_id, "client_id": self.client_id, "ops": ops,
        })
        t_send = time.perf_counter_ns()
        await self._ws.send(frame)
        return mutation_id, t_send, fut
