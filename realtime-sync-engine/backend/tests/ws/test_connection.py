"""WebSocket connect/disconnect, authorization and multi-user presence."""

import pytest
from channels.testing import WebsocketCommunicator

from tests.conftest import INITIAL_CONTENT, insert, mutation, next_message, open_socket

pytestmark = pytest.mark.django_db(transaction=True)


async def _close_code(app, path):
    comm = WebsocketCommunicator(app, path)
    connected, _ = await comm.connect()
    assert connected  # accepted, then closed with an application code
    out = await comm.receive_output(timeout=3)
    assert out["type"] == "websocket.close"
    return out["code"]


async def test_missing_token_closes_4001(ws_app, document):
    assert await _close_code(ws_app, f"/ws/documents/{document.id}/") == 4001


async def test_bad_token_closes_4001(ws_app, document):
    assert await _close_code(ws_app, f"/ws/documents/{document.id}/?token=nope") == 4001


async def test_non_member_closes_4003(ws_app, document, accounts):
    path = f"/ws/documents/{document.id}/?token={accounts['mallory'].token}"
    assert await _close_code(ws_app, path) == 4003


async def test_unknown_document_closes_4004(ws_app, accounts):
    path = f"/ws/documents/00000000-0000-0000-0000-000000000000/?token={accounts['alice'].token}"
    assert await _close_code(ws_app, path) == 4004


async def test_member_receives_snapshot_with_current_version(ws_app, document, accounts):
    comm = await open_socket(ws_app, accounts["bob"], document.id)
    snap = await next_message(comm, "snapshot")
    assert snap["version"] == 0
    assert snap["content"] == INITIAL_CONTENT
    assert snap["role"] == "editor"
    assert snap["user"]["username"] == "bob"
    await comm.disconnect()


async def test_client_id_from_query_is_echoed(ws_app, document, accounts):
    cid = "6f1d0c35-3c1e-4d7b-9f6a-1a2b3c4d5e6f"
    comm = await open_socket(ws_app, accounts["bob"], document.id, client_id=cid)
    assert (await next_message(comm, "snapshot"))["client_id"] == cid
    await comm.disconnect()


async def test_multiple_users_connect_to_one_document_and_see_presence(ws_app, document, accounts):
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    await next_message(alice, "snapshot")
    bob = await open_socket(ws_app, accounts["bob"], document.id)
    await next_message(bob, "snapshot")

    joined = await next_message(alice, "presence")
    assert (joined["event"], joined["username"]) == ("joined", "bob")
    here = await next_message(bob, "presence")  # alice announces herself to the newcomer
    assert (here["event"], here["username"]) == ("here", "alice")

    carol = await open_socket(ws_app, accounts["carol"], document.id)
    await next_message(carol, "snapshot")
    seen = {(await next_message(carol, "presence"))["username"] for _ in range(2)}
    assert seen == {"alice", "bob"}

    await bob.disconnect()
    while True:  # skip carol's "joined" that is still queued for alice
        left = await next_message(alice, "presence")
        if left["event"] == "left":
            break
    assert left["username"] == "bob"
    await alice.disconnect()
    await carol.disconnect()


async def test_disconnected_socket_stops_receiving_updates(ws_app, document, accounts):
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    await next_message(alice, "snapshot")
    bob = await open_socket(ws_app, accounts["bob"], document.id)
    await next_message(bob, "snapshot")
    await bob.disconnect()

    await alice.send_json_to(mutation(document.id, 0, [insert(0, "x")]))
    assert (await next_message(alice, "ack"))["version"] == 1
    # Bob's channel left the group; a fresh socket starts from a snapshot at v1.
    bob2 = await open_socket(ws_app, accounts["bob"], document.id)
    assert (await next_message(bob2, "snapshot"))["version"] == 1
    await alice.disconnect()
    await bob2.disconnect()


async def test_reconnect_with_since_gets_catchup_instead_of_snapshot(ws_app, document, accounts):
    alice = await open_socket(ws_app, accounts["alice"], document.id)
    await next_message(alice, "snapshot")
    for v in range(3):
        await alice.send_json_to(mutation(document.id, v, [insert(0, str(v))]))
        await next_message(alice, "ack")

    bob = await open_socket(ws_app, accounts["bob"], document.id, since=1)
    catchup = await next_message(bob, "catchup")
    assert catchup["from_version"] == 1 and catchup["current_version"] == 3
    assert [r["version"] for r in catchup["revisions"]] == [2, 3]
    assert catchup["role"] == "editor"
    await alice.disconnect()
    await bob.disconnect()
