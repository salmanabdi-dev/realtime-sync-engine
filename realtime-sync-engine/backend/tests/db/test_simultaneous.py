"""Real races: several threads, each with its own PostgreSQL connection, submitting
mutations against the same version at the same moment."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection

import apps.sync.concurrency as concurrency
from apps.documents.models import DocumentRevision, DocumentState
from apps.sync.concurrency import Accepted, Conflict, commit_mutation
from tests.conftest import INITIAL_CONTENT, delete, insert

pytestmark = pytest.mark.django_db(transaction=True)


def _in_thread(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    finally:
        connection.close()  # each thread owns its connection


def _commit(document_id, author_id, expected, ops):
    return _in_thread(
        commit_mutation, document_id=document_id, author_id=author_id, expected_version=expected,
        ops=ops, client_id=uuid.uuid4(), mutation_id=uuid.uuid4(),
    )


def test_two_users_same_version_only_one_wins(document, accounts, monkeypatch):
    """The exact scenario from the design: both read version 10 (here: 0) and both
    pass the fast-path version check before either writes. The barrier forces that
    interleaving, so the compare-and-swap UPDATE is what has to save us."""
    barrier = threading.Barrier(2, timeout=10)
    real_apply = concurrency.apply_operations

    def apply_after_both_have_read(content, ops, max_chars=None):
        barrier.wait()  # both transactions have now read version 0
        return real_apply(content, ops, max_chars)

    monkeypatch.setattr(concurrency, "apply_operations", apply_after_both_have_read)

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(_commit, document.id, accounts["alice"].id, 0, [insert(11, "!")])
        b = pool.submit(_commit, document.id, accounts["bob"].id, 0, [delete(5, 6)])
        results = [a.result(timeout=15), b.result(timeout=15)]

    accepted = [r for r in results if isinstance(r, Accepted)]
    conflicts = [r for r in results if isinstance(r, Conflict)]
    assert len(accepted) == 1 and len(conflicts) == 1
    assert accepted[0].version == 1
    assert (conflicts[0].expected_version, conflicts[0].current_version) == (0, 1)
    assert [r["version"] for r in conflicts[0].missing] == [1]

    s = DocumentState.objects.get(document=document)
    assert s.version == 1
    assert s.content in {"Hello world!", "Hello"}  # exactly one edit applied
    assert DocumentRevision.objects.filter(document=document).count() == 1


@pytest.mark.parametrize("writers", [2, 8])
def test_many_concurrent_writers_on_same_version(document, accounts, writers):
    for round_no in range(10):
        start = threading.Barrier(writers, timeout=10)

        def go(i, expected=round_no):
            start.wait()
            return _commit(document.id, accounts["bob"].id, expected, [insert(0, "x")])

        with ThreadPoolExecutor(writers) as pool:
            results = list(pool.map(go, range(writers)))
        assert sum(isinstance(r, Accepted) for r in results) == 1
        assert sum(isinstance(r, Conflict) for r in results) == writers - 1
    assert DocumentState.objects.get(document=document).version == 10


def test_no_lost_updates_under_contention(document, accounts):
    """Each writer keeps retrying (fetching the newest version on conflict) until all
    of its inserts land. Every insert must survive: version and length both prove it."""
    writers, per_writer = 6, 15

    def writer(i):
        accepted = conflicts = 0
        expected = 0
        while accepted < per_writer:
            result = _commit(document.id, accounts["alice"].id, expected, [insert(0, str(i))])
            if isinstance(result, Accepted):
                accepted += 1
                expected = result.version
            else:
                conflicts += 1
                expected = result.current_version  # resynchronize, then retry
        return conflicts

    with ThreadPoolExecutor(writers) as pool:
        conflicts = sum(pool.map(writer, range(writers)))

    total = writers * per_writer
    s = DocumentState.objects.get(document=document)
    assert s.version == total
    assert len(s.content) == len(INITIAL_CONTENT) + total
    for i in range(writers):
        assert s.content.count(str(i)) == per_writer
    versions = list(DocumentRevision.objects.filter(document=document).values_list("version", flat=True))
    assert versions == list(range(1, total + 1))  # contiguous, no gaps, no duplicates
    print(f"{conflicts} conflicts resolved by retry")
