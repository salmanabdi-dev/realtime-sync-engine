"""DocumentConsumer: the network adapter between one browser tab and the engine.

It authenticates, checks membership, joins the document's Redis group, parses
messages and hands mutations to `concurrency.commit_mutation`. It holds no
document state of its own; PostgreSQL is the source of truth.

Lifecycle
    connect     -> accept, auth (close 4001/4003/4004), group_add, snapshot|catchup
    mutation    -> commit -> publish update to group -> ack sender
                           -> or conflict (to sender only)
    doc_update  -> runs in every consumer of the group; skips the originator
    disconnect  -> group_discard, presence "left"
"""

from __future__ import annotations

import logging
import time
import uuid
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from apps.documents.models import Document
from apps.documents.permissions import can_write
from apps.documents.services import get_role

from . import protocol as p
from .broadcast import group_name, publish_presence, publish_update
from .codec import dumps, loads
from .concurrency import (
    Accepted,
    DocumentMissing,
    InvalidOperation,
    commit_mutation,
    load_snapshot,
    revisions_since,
)
from .metrics import metrics, timed

logger = logging.getLogger("apps.sync")

CLOSE_UNAUTHENTICATED = 4001
CLOSE_FORBIDDEN = 4003
CLOSE_NOT_FOUND = 4004


def _db(func):
    return database_sync_to_async(func, thread_sensitive=settings.SYNC["DB_THREAD_SENSITIVE"])


def _resolve_access(user_id: int, document_id) -> str | None:
    """Return the role, 'missing' if the document does not exist, or None."""
    role = get_role(user_id, document_id)
    if role is not None:
        return role
    return None if Document.objects.filter(pk=document_id).exists() else "missing"


def _initial_state(document_id, since: int | None) -> tuple[str, dict]:
    if since is not None:
        replay = revisions_since(document_id, since)
        if replay is not None:
            current, revisions = replay
            return "catchup", {"from_version": since, "current_version": current, "revisions": revisions}
    return "snapshot", load_snapshot(document_id)


class _RateLimiter:
    """Token bucket: `rate` messages per second with an equal burst allowance."""

    __slots__ = ("rate", "tokens", "stamp")

    def __init__(self, rate: int):
        self.rate = float(rate)
        self.tokens = float(rate)
        self.stamp = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.rate, self.tokens + (now - self.stamp) * self.rate)
        self.stamp = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


class DocumentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.document_id = self.scope["url_route"]["kwargs"]["document_id"]
        self.group = group_name(self.document_id)
        self.joined = False
        self.user = self.scope.get("user")
        params = parse_qs(self.scope.get("query_string", b"").decode())
        self.client_id = self._client_id(params)
        self.limiter = _RateLimiter(settings.SYNC["RATE_LIMIT_PER_SECOND"])

        # Accept first so the browser receives our application close codes;
        # rejecting during the handshake only ever surfaces as code 1006.
        await self.accept()

        if self.user is None or not self.user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        access = await _db(_resolve_access)(self.user.pk, self.document_id)
        if access == "missing":
            await self.close(code=CLOSE_NOT_FOUND)
            return
        if access is None:
            await self.close(code=CLOSE_FORBIDDEN)
            return
        self.role = access

        # Subscribe BEFORE reading state so no accepted update can slip between the
        # read and the subscription. Duplicates are harmless: clients drop any
        # update whose version they already have.
        await self.channel_layer.group_add(self.group, self.channel_name)
        self.joined = True

        since = self._since(params)
        kind, data = await _db(_initial_state)(self.document_id, since)
        session = p.session_info(self.role, self.client_id, self.user.pk, self.user.username)
        if kind == "catchup":
            msg = p.catchup_message(self.document_id, data["from_version"], data["current_version"],
                                    data["revisions"], session)
        else:
            msg = p.snapshot_message(self.document_id, data["version"], data["content"], session)
        await self._send(msg)
        metrics.incr("connections_opened")
        await publish_presence(self.channel_layer, self.document_id, "joined", self.channel_name,
                               user_id=self.user.pk, username=self.user.username,
                               client_id=self.client_id)

    async def disconnect(self, code):
        if getattr(self, "joined", False):
            self.joined = False
            await self.channel_layer.group_discard(self.group, self.channel_name)
            metrics.incr("connections_closed")
            await publish_presence(self.channel_layer, self.document_id, "left", self.channel_name,
                                   user_id=self.user.pk, username=self.user.username,
                                   client_id=self.client_id)

    # ------------------------------------------------------------------ inbound

    async def receive(self, text_data=None, bytes_data=None):
        if not self.joined:
            return
        if not self.limiter.allow():
            await self._send(p.error_message("rate_limited", "Too many messages; slow down."))
            return
        started = time.perf_counter()
        try:
            msg = p.parse_client_message(loads(text_data if text_data is not None else bytes_data))
        except p.ProtocolError as exc:
            await self._send(p.error_message(exc.code, exc.message, exc.mutation_id))
            return
        except ValueError:
            await self._send(p.error_message("invalid_json", "Message is not valid JSON."))
            return
        metrics.record("parse", (time.perf_counter() - started) * 1000.0)

        if isinstance(msg, p.MutationMessage):
            await self._handle_mutation(msg, started)
        elif isinstance(msg, p.ResyncMessage):
            await self._handle_resync(msg)
        else:
            await self._send(p.pong_message(msg.ts))

    async def _handle_mutation(self, msg: p.MutationMessage, started: float) -> None:
        mutation_id = str(msg.mutation_id)
        if msg.document_id != self.document_id:
            await self._send(p.error_message("wrong_document", "Mutation is for another document.", mutation_id))
            return
        if not can_write(self.role):
            await self._send(p.error_message("forbidden", "You have read-only access.", mutation_id))
            return

        t0 = time.perf_counter()
        try:
            result = await _db(commit_mutation)(
                document_id=self.document_id,
                author_id=self.user.pk,
                expected_version=msg.expected_version,
                ops=msg.ops,
                client_id=msg.client_id,
                mutation_id=msg.mutation_id,
            )
        except InvalidOperation as exc:
            await self._send(p.error_message("invalid_operation", str(exc), mutation_id))
            return
        except DocumentMissing:
            await self.close(code=CLOSE_NOT_FOUND)
            return
        metrics.record("commit", (time.perf_counter() - t0) * 1000.0)

        if isinstance(result, Accepted):
            if not result.duplicate:
                # Publish first: other users' latency is what we optimize for.
                t1 = time.perf_counter()
                update = p.update_message(self.document_id, result.version, result.ops,
                                          result.author_id, result.client_id, result.mutation_id)
                await publish_update(self.channel_layer, self.document_id, update, self.channel_name)
                metrics.record("publish", (time.perf_counter() - t1) * 1000.0)
                metrics.incr("mutations_accepted")
            else:
                metrics.incr("mutations_duplicate")
            await self._send(p.ack_message(result.mutation_id, result.version, result.duplicate))
        else:
            metrics.incr("mutations_conflicted")
            await self._send(p.conflict_message(msg.mutation_id, result.expected_version,
                                                result.current_version, result.missing, result.snapshot))
        metrics.record("handle_total", (time.perf_counter() - started) * 1000.0)

    async def _handle_resync(self, msg: p.ResyncMessage) -> None:
        try:
            replay = await _db(revisions_since)(self.document_id, msg.since_version)
            if replay is not None:
                current, revisions = replay
                await self._send(p.catchup_message(self.document_id, msg.since_version, current, revisions))
                return
            snap = await _db(load_snapshot)(self.document_id)
        except DocumentMissing:
            await self.close(code=CLOSE_NOT_FOUND)
            return
        session = p.session_info(self.role, self.client_id, self.user.pk, self.user.username)
        await self._send(p.snapshot_message(self.document_id, snap["version"], snap["content"], session))

    # ------------------------------------------------------------------ group events

    async def doc_update(self, event):
        if event["origin"] == self.channel_name:
            return  # the sender gets an ack instead
        await self.send(text_data=event["text"])

    async def doc_presence(self, event):
        if event["origin"] == self.channel_name:
            return
        await self._send(p.presence_message(event["event"], event["user_id"], event["username"],
                                            event["client_id"]))
        if event["event"] == "joined":
            # Tell the newcomer we're here (direct message, not a group broadcast).
            await self.channel_layer.send(
                event["origin"],
                {"type": "doc.presence", "event": "here", "origin": self.channel_name,
                 "user_id": self.user.pk, "username": self.user.username,
                 "client_id": self.client_id},
            )

    # ------------------------------------------------------------------ helpers

    async def _send(self, message: dict) -> None:
        await self.send(text_data=dumps(message))

    @staticmethod
    def _client_id(params) -> str:
        raw = (params.get("client_id") or [None])[0]
        try:
            return str(uuid.UUID(raw)) if raw else str(uuid.uuid4())
        except ValueError:
            return str(uuid.uuid4())

    @staticmethod
    def _since(params) -> int | None:
        raw = (params.get("since") or [None])[0]
        if raw is None or not raw.isdigit():
            return None
        return int(raw)
