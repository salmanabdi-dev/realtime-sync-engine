"""Optimistic concurrency control for document mutations.

The guarantee lives in one SQL statement:

    UPDATE document_states SET content = %s, version = version + 1
     WHERE document_id = %s AND version = %s          -- expected_version

PostgreSQL executes the comparison and the write atomically. If two transactions
race on the same expected version, the second UPDATE blocks on the row lock held
by the first; when the first commits, PostgreSQL (READ COMMITTED) re-evaluates the
WHERE clause against the newly committed row, finds version = expected + 1, and
updates zero rows. That transaction then reports a conflict. Nothing it computed
from the stale state is ever written.

This module has no knowledge of WebSockets; it is called from the consumer through
a thread pool and can be tested directly with threads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F

from apps.documents.models import DocumentRevision, DocumentState

from .operations import InvalidOperation, apply_operations

__all__ = [
    "Accepted",
    "Conflict",
    "DocumentMissing",
    "InvalidOperation",
    "commit_mutation",
    "load_snapshot",
    "revisions_since",
]


class DocumentMissing(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class Accepted:
    version: int
    ops: list[dict]
    author_id: int
    client_id: uuid.UUID
    mutation_id: uuid.UUID
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class Conflict:
    expected_version: int
    current_version: int
    missing: list[dict] | None = None   # revisions expected+1..current, oldest first
    snapshot: dict | None = field(default=None)  # used when replay is too long

    @property
    def needs_snapshot(self) -> bool:
        return self.snapshot is not None


def _max_catchup() -> int:
    return settings.SYNC["MAX_CATCHUP_REVISIONS"]


def _find_revision(document_id, mutation_id) -> DocumentRevision | None:
    return DocumentRevision.objects.filter(document_id=document_id, mutation_id=mutation_id).first()


def _accepted_from(rev: DocumentRevision) -> Accepted:
    return Accepted(
        version=rev.version,
        ops=rev.operations,
        author_id=rev.author_id,
        client_id=rev.client_id,
        mutation_id=rev.mutation_id,
        duplicate=True,
    )


def load_snapshot(document_id) -> dict:
    row = DocumentState.objects.filter(document_id=document_id).values_list("version", "content").first()
    if row is None:
        raise DocumentMissing(str(document_id))
    return {"version": row[0], "content": row[1]}


def revisions_since(document_id, since_version: int, limit: int | None = None) -> tuple[int, list[dict]] | None:
    """Revisions after `since_version`, or None if a replay is not possible
    (client claims a future version, or is too far behind)."""
    limit = _max_catchup() if limit is None else limit
    current = DocumentState.objects.filter(document_id=document_id).values_list("version", flat=True).first()
    if current is None:
        raise DocumentMissing(str(document_id))
    if since_version > current or current - since_version > limit:
        return None
    revs = DocumentRevision.objects.filter(
        document_id=document_id, version__gt=since_version, version__lte=current
    ).order_by("version")
    revisions = [r.as_wire() for r in revs]
    if len(revisions) != current - since_version:
        return None  # history incomplete (should not happen); force a snapshot
    return current, revisions


def _conflict(document_id, mutation_id, expected_version: int) -> Accepted | Conflict:
    # The mutation may have been applied by an earlier (retried) send that committed
    # while we were running. A fresh statement in READ COMMITTED sees that commit.
    prior = _find_revision(document_id, mutation_id)
    if prior is not None:
        return _accepted_from(prior)
    replay = revisions_since(document_id, expected_version)
    if replay is None:
        snap = load_snapshot(document_id)
        return Conflict(expected_version, snap["version"], snapshot=snap)
    current, revisions = replay
    return Conflict(expected_version, current, missing=revisions)


def commit_mutation(
    *,
    document_id,
    author_id: int,
    expected_version: int,
    ops: list[dict],
    client_id: uuid.UUID,
    mutation_id: uuid.UUID,
) -> Accepted | Conflict:
    """Apply `ops` iff the document is still at `expected_version`.

    Returns Accepted (new version) or Conflict (with what the client needs to
    resynchronize). Raises InvalidOperation for ops that don't fit the text and
    DocumentMissing if the document does not exist.
    """
    try:
        with transaction.atomic():
            # 0. Idempotency: a retried send of an already-applied mutation.
            prior = _find_revision(document_id, mutation_id)
            if prior is not None:
                return _accepted_from(prior)

            # 1. Plain read (no lock, readers never block).
            row = (
                DocumentState.objects.filter(document_id=document_id)
                .values_list("content", "version")
                .first()
            )
            if row is None:
                raise DocumentMissing(str(document_id))
            content, current_version = row

            # 2. Fast path: already stale. Reject without writing.
            if current_version != expected_version:
                return _conflict(document_id, mutation_id, expected_version)

            # 3. Apply in memory (pure function, may raise InvalidOperation).
            new_content = apply_operations(content, ops, settings.SYNC["MAX_DOCUMENT_CHARS"])

            # 4. Atomic compare-and-swap: the actual concurrency guarantee.
            updated = DocumentState.objects.filter(
                document_id=document_id, version=expected_version
            ).update(content=new_content, version=F("version") + 1)
            if updated == 0:
                # Lost the race between step 1 and step 4.
                return _conflict(document_id, mutation_id, expected_version)

            # 5. Record the revision in the same transaction.
            DocumentRevision.objects.create(
                document_id=document_id,
                version=expected_version + 1,
                author_id=author_id,
                client_id=client_id,
                mutation_id=mutation_id,
                operations=ops,
            )
    except IntegrityError:
        # A unique constraint fired: either this mutation_id was committed by a
        # concurrent retry, or (only possible if step 4 were bypassed) the version
        # already exists. Both resolve the same way as a conflict.
        return _conflict(document_id, mutation_id, expected_version)

    # 6. Committed. The caller acks and broadcasts.
    return Accepted(
        version=expected_version + 1,
        ops=ops,
        author_id=author_id,
        client_id=client_id,
        mutation_id=mutation_id,
    )
