"""Optimistic concurrency: accept, version increment, stale rejection, idempotency."""

import uuid

import pytest

from apps.documents.models import DocumentRevision, DocumentState
from apps.sync.concurrency import (
    Accepted,
    Conflict,
    InvalidOperation,
    commit_mutation,
    revisions_since,
)
from tests.conftest import INITIAL_CONTENT, delete, insert

pytestmark = pytest.mark.django_db


def commit(document, author, expected, ops, mutation_id=None):
    return commit_mutation(
        document_id=document.id, author_id=author.id, expected_version=expected, ops=ops,
        client_id=uuid.uuid4(), mutation_id=mutation_id or uuid.uuid4(),
    )


def state(document):
    s = DocumentState.objects.get(document=document)
    return s.content, s.version


def test_accepted_mutation_increments_version_and_persists(document, accounts):
    result = commit(document, accounts["alice"], 0, [insert(5, ",")])
    assert isinstance(result, Accepted) and result.version == 1 and not result.duplicate
    assert state(document) == ("Hello, world", 1)
    rev = DocumentRevision.objects.get(document=document, version=1)
    assert rev.author_id == accounts["alice"].id
    assert rev.operations == [insert(5, ",")]


def test_versions_increase_by_exactly_one_per_accepted_mutation(document, accounts):
    for expected in range(5):
        result = commit(document, accounts["bob"], expected, [insert(0, str(expected))])
        assert result.version == expected + 1
    assert state(document) == ("43210" + INITIAL_CONTENT, 5)
    assert list(DocumentRevision.objects.filter(document=document).values_list("version", flat=True)) == [1, 2, 3, 4, 5]


def test_stale_mutation_is_rejected_with_missing_revisions(document, accounts):
    commit(document, accounts["alice"], 0, [insert(11, "!")])
    result = commit(document, accounts["bob"], 0, [delete(5, 6)])
    assert isinstance(result, Conflict)
    assert (result.expected_version, result.current_version) == (0, 1)
    assert [r["version"] for r in result.missing] == [1]
    assert result.missing[0]["ops"] == [insert(11, "!")]
    assert result.snapshot is None
    # Nothing from the stale mutation was written.
    assert state(document) == ("Hello world!", 1)
    assert DocumentRevision.objects.filter(document=document).count() == 1


def test_resubmitting_after_reconciling_succeeds(document, accounts):
    commit(document, accounts["alice"], 0, [insert(11, "!")])
    conflict = commit(document, accounts["bob"], 0, [delete(5, 6)])
    retry = commit(document, accounts["bob"], conflict.current_version, [delete(5, 6)])
    assert isinstance(retry, Accepted) and retry.version == 2
    assert state(document) == ("Hello!", 2)


def test_far_behind_client_gets_snapshot_instead_of_replay(document, accounts, settings):
    settings.SYNC = {**settings.SYNC, "MAX_CATCHUP_REVISIONS": 2}
    for v in range(3):
        commit(document, accounts["alice"], v, [insert(0, "x")])
    result = commit(document, accounts["bob"], 0, [insert(0, "y")])
    assert isinstance(result, Conflict)
    assert result.missing is None
    assert result.snapshot == {"version": 3, "content": "xxx" + INITIAL_CONTENT}


def test_future_expected_version_gets_snapshot(document, accounts):
    result = commit(document, accounts["bob"], 99, [insert(0, "y")])
    assert isinstance(result, Conflict) and result.snapshot["version"] == 0


def test_retried_mutation_is_idempotent(document, accounts):
    mid = uuid.uuid4()
    first = commit(document, accounts["alice"], 0, [insert(0, "A")], mutation_id=mid)
    # Same mutation re-sent (e.g. after a reconnect), even with a stale version.
    again = commit(document, accounts["alice"], 0, [insert(0, "A")], mutation_id=mid)
    assert isinstance(again, Accepted) and again.duplicate and again.version == first.version == 1
    assert state(document) == ("A" + INITIAL_CONTENT, 1)


def test_invalid_operation_changes_nothing(document, accounts):
    with pytest.raises(InvalidOperation):
        commit(document, accounts["alice"], 0, [insert(500, "x")])
    assert state(document) == (INITIAL_CONTENT, 0)
    assert not DocumentRevision.objects.filter(document=document).exists()


def test_revisions_since(document, accounts):
    for v in range(3):
        commit(document, accounts["alice"], v, [insert(0, str(v))])
    current, revs = revisions_since(document.id, 1)
    assert current == 3 and [r["version"] for r in revs] == [2, 3]
    assert revisions_since(document.id, 3) == (3, [])
    assert revisions_since(document.id, 4) is None
