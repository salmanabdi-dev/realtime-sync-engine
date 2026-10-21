"""WebSocket message contract (see docs/protocol.md).

Parsing is hand-rolled rather than schema-library based: it is a few dozen lines,
has no dependencies, and sits on the hot path of every mutation.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

MAX_OPS_PER_MUTATION = 100
MAX_INSERT_CHARS = 10_000


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str, mutation_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.mutation_id = mutation_id


# --------------------------------------------------------------------------- client → server


@dataclass(frozen=True, slots=True)
class MutationMessage:
    document_id: uuid.UUID
    expected_version: int
    mutation_id: uuid.UUID
    client_id: uuid.UUID
    ops: list[dict]


@dataclass(frozen=True, slots=True)
class ResyncMessage:
    since_version: int


@dataclass(frozen=True, slots=True)
class PingMessage:
    ts: float | None


ClientMessage = MutationMessage | ResyncMessage | PingMessage


def _int(value: Any, field: str, minimum: int = 0) -> int:
    # bool is a subclass of int in Python; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("invalid_message", f"{field} must be an integer")
    if value < minimum:
        raise ProtocolError("invalid_message", f"{field} must be >= {minimum}")
    return value


def _uuid(value: Any, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise ProtocolError("invalid_message", f"{field} must be a UUID string")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ProtocolError("invalid_message", f"{field} must be a UUID string") from exc


def _valid_text(text: str) -> bool:
    try:
        text.encode("utf-8")  # rejects lone surrogates, which PostgreSQL cannot store
    except UnicodeEncodeError:
        return False
    return "\x00" not in text  # PostgreSQL text cannot hold NUL


def parse_op(raw: Any, max_insert_chars: int = MAX_INSERT_CHARS) -> dict:
    if not isinstance(raw, dict):
        raise ProtocolError("invalid_operation", "each op must be an object")
    kind = raw.get("kind")
    pos = _int(raw.get("pos"), "op.pos")
    if kind == "insert":
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            raise ProtocolError("invalid_operation", "insert.text must be a non-empty string")
        if len(text) > max_insert_chars:
            raise ProtocolError("invalid_operation", f"insert.text exceeds {max_insert_chars} chars")
        if not _valid_text(text):
            raise ProtocolError("invalid_operation", "insert.text contains invalid characters")
        return {"kind": "insert", "pos": pos, "text": text}
    if kind == "delete":
        return {"kind": "delete", "pos": pos, "length": _int(raw.get("length"), "delete.length", 1)}
    raise ProtocolError("invalid_operation", "op.kind must be 'insert' or 'delete'")


def parse_client_message(data: Any, max_ops: int = MAX_OPS_PER_MUTATION) -> ClientMessage:
    if not isinstance(data, dict):
        raise ProtocolError("invalid_message", "message must be a JSON object")
    kind = data.get("type")
    if kind == "mutation":
        mutation_id = data.get("mutation_id") if isinstance(data.get("mutation_id"), str) else None
        try:
            raw_ops = data.get("ops")
            if not isinstance(raw_ops, list) or not raw_ops:
                raise ProtocolError("invalid_message", "ops must be a non-empty list")
            if len(raw_ops) > max_ops:
                raise ProtocolError("invalid_message", f"at most {max_ops} ops per mutation")
            return MutationMessage(
                document_id=_uuid(data.get("document_id"), "document_id"),
                expected_version=_int(data.get("expected_version"), "expected_version"),
                mutation_id=_uuid(data.get("mutation_id"), "mutation_id"),
                client_id=_uuid(data.get("client_id"), "client_id"),
                ops=[parse_op(op) for op in raw_ops],
            )
        except ProtocolError as exc:
            exc.mutation_id = mutation_id
            raise
    if kind == "resync":
        return ResyncMessage(since_version=_int(data.get("since_version"), "since_version"))
    if kind == "ping":
        ts = data.get("ts")
        return PingMessage(ts=ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None)
    raise ProtocolError("unknown_type", f"unknown message type {kind!r}")


# --------------------------------------------------------------------------- server → client


def now_ms() -> float:
    return time.time() * 1000.0


def session_info(role: str, client_id: str, user_id: int, username: str) -> dict:
    return {"role": role, "client_id": client_id, "user": {"id": user_id, "username": username}}


def snapshot_message(document_id, version: int, content: str, session: dict) -> dict:
    return {"type": "snapshot", "document_id": str(document_id), "version": version,
            "content": content, **session}


def catchup_message(document_id, from_version: int, current_version: int,
                    revisions: list[dict], session: dict | None = None) -> dict:
    msg = {"type": "catchup", "document_id": str(document_id), "from_version": from_version,
           "current_version": current_version, "revisions": revisions}
    if session:
        msg.update(session)
    return msg


def ack_message(mutation_id, version: int, duplicate: bool = False) -> dict:
    return {"type": "ack", "mutation_id": str(mutation_id), "version": version,
            "duplicate": duplicate}


def update_message(document_id, version: int, ops: list[dict], author_id: int,
                   client_id, mutation_id) -> dict:
    return {"type": "update", "document_id": str(document_id), "version": version, "ops": ops,
            "author_id": author_id, "client_id": str(client_id),
            "mutation_id": str(mutation_id), "server_ts": now_ms()}


def conflict_message(mutation_id, expected_version: int, current_version: int,
                     missing: list[dict] | None, snapshot: dict | None) -> dict:
    return {"type": "conflict", "mutation_id": str(mutation_id),
            "expected_version": expected_version, "current_version": current_version,
            "missing": missing, "snapshot": snapshot}


def error_message(code: str, message: str, mutation_id: str | None = None) -> dict:
    msg = {"type": "error", "code": code, "message": message}
    if mutation_id:
        msg["mutation_id"] = mutation_id
    return msg


def presence_message(event: str, user_id: int, username: str, client_id: str) -> dict:
    return {"type": "presence", "event": event, "user_id": user_id, "username": username,
            "client_id": client_id}


def pong_message(ts: float | None) -> dict:
    return {"type": "pong", "ts": ts, "server_ts": now_ms()}
