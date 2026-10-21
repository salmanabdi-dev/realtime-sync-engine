"""Real-time propagation through Channels + Redis, and persistence of what was broadcast."""

import uuid

import pytest
from channels.db import database_sync_to_async

from apps.documents.models import DocumentRevision, DocumentState
from tests.conftest import INITIAL_CONTENT, delete, drain, insert, mutation, next_message, open_socket

pytestmark = pytest.mark.django_db(transaction=True)


@database_sync_to_async
def db_state(document):
    s = DocumentState.objects.get(document=document)
    return s.content, s.version


async def _three(ws_app, document, accounts):
    socks = {}
    for name in ("alice", "bob", "carol"):
        socks[name] = await open_socket(ws_app, accounts[name], document.id)
        await next_message(socks[name], "snapshot")
    return socks


async def _close(socks):
    for s in socks.values():
        await s.disconnect()


async def test_update_reaches_every_other_client_and_sender_gets_ack(ws_app, document, accounts):
    s = await _three(ws_app, document, accounts)
    client_id = uuid.uuid4()
    msg = mutation(document.id, 0, [insert(5, ",")], client_id=client_id)
    await s["alice"].send_json_to(msg)

    ack = await next_message(s["alice"], "ack")
    assert ack == {"type": "ack", "mutation_id": msg["mutation_id"], "version": 1, "duplicate": False}
    for name in ("bob", "carol"):
        update = await next_message(s[name], "update")
        assert update["version"] == 1
        assert update["ops"] == [insert(5, ",")]
        assert update["author_id"] == accounts["alice"].id
        assert update["client_id"] == str(client_id)
        assert update["mutation_id"] == msg["mutation_id"]

    # The sender never receives its own update (it already has the change).
    assert not [m for m in await drain(s["alice"]) if m["type"] == "update"]
    await _close(s)


async def test_broadcast_state_is_persisted(ws_app, document, accounts):
    s = await _three(ws_app, document, accounts)
    await s["bob"].send_json_to(mutation(document.id, 0, [delete(5, 6), insert(5, "!")]))
    await next_message(s["bob"], "ack")
    await next_message(s["alice"], "update")
    assert await db_state(document) == ("Hello!", 1)
    assert await database_sync_to_async(DocumentRevision.objects.filter(document=document).count)() == 1
    await _close(s)


async def test_sequential_updates_arrive_in_version_order(ws_app, document, accounts):
    s = await _three(ws_app, document, accounts)
    for v in range(10):
        await s["alice"].send_json_to(mutation(document.id, v, [insert(0, str(v))]))
        assert (await next_message(s["alice"], "ack"))["version"] == v + 1
    versions = [(await next_message(s["bob"], "update"))["version"] for _ in range(10)]
    assert versions == list(range(1, 11))
    assert await db_state(document) == ("9876543210" + INITIAL_CONTENT, 10)
    await _close(s)


async def test_viewer_cannot_mutate(ws_app, document, accounts):
    s = await _three(ws_app, document, accounts)
    msg = mutation(document.id, 0, [insert(0, "x")])
    await s["carol"].send_json_to(msg)
    err = await next_message(s["carol"], "error")
    assert err["code"] == "forbidden" and err["mutation_id"] == msg["mutation_id"]
    assert await db_state(document) == (INITIAL_CONTENT, 0)
    await _close(s)


async def test_invalid_operation_returns_error_and_broadcasts_nothing(ws_app, document, accounts):
    s = await _three(ws_app, document, accounts)
    await s["alice"].send_json_to(mutation(document.id, 0, [insert(999, "x")]))
    assert (await next_message(s["alice"], "error"))["code"] == "invalid_operation"
    assert not [m for m in await drain(s["bob"]) if m["type"] == "update"]
    await _close(s)


async def test_malformed_messages_get_errors_not_disconnects(ws_app, document, accounts):
    sock = await open_socket(ws_app, accounts["alice"], document.id)
    await next_message(sock, "snapshot")
    await sock.send_to(text_data="{not json")
    assert (await next_message(sock, "error"))["code"] == "invalid_json"
    await sock.send_json_to({"type": "mutation", "ops": "nope"})
    assert (await next_message(sock, "error"))["code"] == "invalid_message"
    await sock.send_json_to({"type": "ping", "ts": 42})
    pong = await next_message(sock, "pong")
    assert pong["ts"] == 42 and pong["server_ts"] > 0
    await sock.disconnect()


async def test_mutation_for_another_document_is_rejected(ws_app, document, accounts):
    sock = await open_socket(ws_app, accounts["alice"], document.id)
    await next_message(sock, "snapshot")
    await sock.send_json_to(mutation(uuid.uuid4(), 0, [insert(0, "x")]))
    assert (await next_message(sock, "error"))["code"] == "wrong_document"
    await sock.disconnect()
