"""Shared fixtures. Tests run against real PostgreSQL and Redis (see docker-compose)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from apps.documents.models import Role
from apps.documents.services import add_member, create_document

User = get_user_model()

INITIAL_CONTENT = "Hello world"


@dataclass
class Account:
    user: object
    token: str

    @property
    def id(self) -> int:
        return self.user.pk

    @property
    def username(self) -> str:
        return self.user.username


def _make_account(username: str) -> Account:
    user = User.objects.create_user(username=username, password="pw")
    return Account(user=user, token=Token.objects.create(user=user).key)


@pytest.fixture
def accounts():
    """alice=owner, bob=editor, carol=viewer, mallory=no access."""
    return {name: _make_account(name) for name in ("alice", "bob", "carol", "mallory")}


@pytest.fixture
def document(accounts):
    doc = create_document(accounts["alice"].user, "Shared doc", INITIAL_CONTENT)
    add_member(doc, accounts["bob"].user, Role.EDITOR)
    add_member(doc, accounts["carol"].user, Role.VIEWER)
    return doc


@pytest.fixture
def ws_app():
    from apps.sync.routing import build_websocket_application

    return build_websocket_application()


def mutation(document_id, expected_version: int, ops: list[dict], *, client_id=None, mutation_id=None) -> dict:
    return {
        "type": "mutation",
        "document_id": str(document_id),
        "expected_version": expected_version,
        "mutation_id": str(mutation_id or uuid.uuid4()),
        "client_id": str(client_id or uuid.uuid4()),
        "ops": ops,
    }


def insert(pos: int, text: str) -> dict:
    return {"kind": "insert", "pos": pos, "text": text}


def delete(pos: int, length: int) -> dict:
    return {"kind": "delete", "pos": pos, "length": length}


async def open_socket(app, account: Account, document_id, **query) -> WebsocketCommunicator:
    params = {"token": account.token, **{k: str(v) for k, v in query.items()}}
    comm = WebsocketCommunicator(app, f"/ws/documents/{document_id}/?{urlencode(params)}")
    connected, _ = await comm.connect(timeout=5)
    assert connected
    return comm


async def next_message(comm: WebsocketCommunicator, *types: str, timeout: float = 3.0) -> dict:
    """Receive until a message of one of `types` arrives (presence noise is skipped)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for {types}")
        msg = await comm.receive_json_from(timeout=remaining)
        if not types or msg["type"] in types:
            return msg


async def drain(comm: WebsocketCommunicator, settle: float = 0.15) -> list[dict]:
    """Collect everything that arrives within `settle` seconds of silence."""
    out = []
    while True:
        try:
            out.append(await comm.receive_json_from(timeout=settle))
        except asyncio.TimeoutError:
            return out
