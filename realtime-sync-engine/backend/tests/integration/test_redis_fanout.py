"""Redis-backed fan-out.

1. Layer level: two independent channel-layer instances (as two server processes
   would have) exchange a group message only through Redis.
2. Process level: two real uvicorn servers; a mutation sent to server A reaches a
   client connected to server B. This is the production topology.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

from apps.documents.models import DocumentState
from tests.conftest import insert, mutation

BACKEND_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    settings.CHANNEL_LAYER_BACKEND == "memory", reason="requires the Redis channel layer"
)


async def test_group_message_crosses_independent_layer_instances():
    from channels_redis.pubsub import RedisPubSubChannelLayer

    hosts = [settings.REDIS_URL]
    process_a = RedisPubSubChannelLayer(hosts=hosts)
    process_b = RedisPubSubChannelLayer(hosts=hosts)
    group = f"doc.fanout-{uuid.uuid4().hex}"
    try:
        receiver = await process_b.new_channel()
        await process_b.group_add(group, receiver)
        await asyncio.sleep(0.1)  # let SUBSCRIBE settle
        await process_a.group_send(group, {"type": "doc.update", "text": "hello", "origin": "a"})
        message = await asyncio.wait_for(process_b.receive(receiver), timeout=3)
        assert message["text"] == "hello"
    finally:
        await process_a.flush()
        await process_b.flush()


# --------------------------------------------------------------------------- process level


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_healthy(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server on {port} exited: {proc.stdout.read().decode()[-2000:]}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health/", timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server on {port} did not become healthy")


@pytest.fixture
def two_servers(transactional_db):
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.test",
        # Point the child processes at the pytest-managed test database.
        "POSTGRES_DB": connection.settings_dict["NAME"],
    }
    ports, procs = [_free_port(), _free_port()], []
    try:
        for port in ports:
            procs.append(subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "config.asgi:application", "--host", "127.0.0.1",
                 "--port", str(port), "--ws", "websockets", "--no-access-log", "--log-level", "warning"],
                cwd=BACKEND_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            ))
        for port, proc in zip(ports, procs):
            _wait_healthy(port, proc)
        yield ports
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


async def _recv(ws, *types, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(remaining, 0.01)))
        if msg["type"] in types:
            return msg


@pytest.mark.integration
async def test_mutation_on_server_a_reaches_client_on_server_b(two_servers, document, accounts):
    from channels.db import database_sync_to_async
    from websockets.asyncio.client import connect

    port_a, port_b = two_servers
    url = "ws://127.0.0.1:{port}/ws/documents/{doc}/?token={token}"
    async with connect(url.format(port=port_a, doc=document.id, token=accounts["alice"].token)) as a, \
               connect(url.format(port=port_b, doc=document.id, token=accounts["bob"].token)) as b:
        await _recv(a, "snapshot")
        await _recv(b, "snapshot")
        msg = mutation(document.id, 0, [insert(0, ">> ")])
        await a.send(json.dumps(msg))
        assert (await _recv(a, "ack"))["version"] == 1
        update = await _recv(b, "update")  # only path from A to B is Redis
        assert update["version"] == 1 and update["mutation_id"] == msg["mutation_id"]

        # And the reverse direction, so both processes publish and subscribe.
        await b.send(json.dumps(mutation(document.id, 1, [insert(0, "<< ")])))
        assert (await _recv(b, "ack"))["version"] == 2
        assert (await _recv(a, "update"))["version"] == 2

    state = await database_sync_to_async(DocumentState.objects.get)(document=document)
    assert (state.content, state.version) == ("<< >> Hello world", 2)
