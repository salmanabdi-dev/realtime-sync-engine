"""Simultaneous mutations over WebSockets: one wins, the other gets a conflict,
reconciles and resubmits, and every client converges."""

import asyncio

import pytest
from channels.db import database_sync_to_async

from apps.documents.models import DocumentState
from tests.conftest import delete, insert, mutation, next_message, open_socket

pytestmark = pytest.mark.django_db(transaction=True)


@database_sync_to_async
def db_state(document):
    s = DocumentState.objects.get(document=document)
    return s.content, s.version


def apply(content, ops):
    for op in ops:
        if op["kind"] == "insert":
            content = content[: op["pos"]] + op["text"] + content[op["pos"]:]
        else:
            content = content[: op["pos"]] + content[op["pos"] + op["length"]:]
    return content


async def test_simultaneous_mutations_one_ack_one_conflict_then_recovery(ws_app, document, accounts):
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    bob = await open_socket(ws_app, accounts["bob"], document.id)
    carol = await open_socket(ws_app, accounts["carol"], document.id)
    for s in (alice, bob, carol):
        assert (await next_message(s, "snapshot"))["version"] == 0

    a_msg = mutation(document.id, 0, [insert(11, "!")])
    b_msg = mutation(document.id, 0, [delete(5, 6)])
    await asyncio.gather(alice.send_json_to(a_msg), bob.send_json_to(b_msg))

    a_reply = await next_message(alice, "ack", "conflict")
    b_reply = await next_message(bob, "ack", "conflict")
    replies = {a_reply["type"], b_reply["type"]}
    assert replies == {"ack", "conflict"}

    winner_reply, loser_sock, loser_msg = (
        (a_reply, bob, b_msg) if a_reply["type"] == "ack" else (b_reply, alice, a_msg)
    )
    conflict = b_reply if a_reply["type"] == "ack" else a_reply
    assert winner_reply["version"] == 1
    assert conflict["mutation_id"] == loser_msg["mutation_id"]
    assert (conflict["expected_version"], conflict["current_version"]) == (0, 1)
    assert len(conflict["missing"]) == 1 and conflict["missing"][0]["version"] == 1

    # Loser reconciles: here the two edits don't overlap, so its ops still apply
    # unchanged on top of version 1. (The TS client does the general rebase.)
    retry = mutation(document.id, conflict["current_version"], loser_msg["ops"])
    await loser_sock.send_json_to(retry)
    assert (await next_message(loser_sock, "ack"))["version"] == 2

    # Carol (passive viewer) sees both versions, in order, and converges.
    text = "Hello world"
    for expected_version in (1, 2):
        upd = await next_message(carol, "update")
        assert upd["version"] == expected_version
        text = apply(text, upd["ops"])
    assert text == "Hello!"
    assert await db_state(document) == ("Hello!", 2)

    for s in (alice, bob, carol):
        await s.disconnect()


async def test_conflict_after_several_missed_versions_lists_them_all(ws_app, document, accounts):
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    bob = await open_socket(ws_app, accounts["bob"], document.id)
    await next_message(alice, "snapshot")
    await next_message(bob, "snapshot")
    for v in range(3):
        await alice.send_json_to(mutation(document.id, v, [insert(0, "a")]))
        await next_message(alice, "ack")

    await bob.send_json_to(mutation(document.id, 0, [insert(0, "b")]))
    conflict = await next_message(bob, "conflict")
    assert [r["version"] for r in conflict["missing"]] == [1, 2, 3]
    await alice.disconnect()
    await bob.disconnect()


async def test_conflict_falls_back_to_snapshot_when_far_behind(ws_app, document, accounts, settings):
    settings.SYNC = {**settings.SYNC, "MAX_CATCHUP_REVISIONS": 1}
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    await next_message(alice, "snapshot")
    for v in range(3):
        await alice.send_json_to(mutation(document.id, v, [insert(0, "a")]))
        await next_message(alice, "ack")
    await alice.send_json_to(mutation(document.id, 0, [insert(0, "z")]))
    conflict = await next_message(alice, "conflict")
    assert conflict["missing"] is None
    assert conflict["snapshot"] == {"version": 3, "content": "aaaHello world"}
    await alice.disconnect()


async def test_explicit_resync_returns_catchup(ws_app, document, accounts):
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    await next_message(alice, "snapshot")
    for v in range(2):
        await alice.send_json_to(mutation(document.id, v, [insert(0, "a")]))
        await next_message(alice, "ack")
    await alice.send_json_to({"type": "resync", "since_version": 0})
    catchup = await next_message(alice, "catchup")
    assert [r["version"] for r in catchup["revisions"]] == [1, 2]
    await alice.disconnect()


async def test_duplicate_send_is_acked_without_rebroadcast(ws_app, document, accounts):
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    bob = await open_socket(ws_app, accounts["bob"], document.id)
    await next_message(alice, "snapshot")
    await next_message(bob, "snapshot")
    msg = mutation(document.id, 0, [insert(0, "a")])
    await alice.send_json_to(msg)
    await next_message(alice, "ack")
    await next_message(bob, "update")

    await alice.send_json_to(msg)  # e.g. resent after a flaky reconnect
    ack = await next_message(alice, "ack")
    assert ack["duplicate"] is True and ack["version"] == 1
    with pytest.raises(AssertionError):
        await next_message(bob, "update", timeout=0.3)
    assert await db_state(document) == ("aHello world", 1)
    await alice.disconnect()
    await bob.disconnect()
