"""PostgreSQL persistence and schema constraints."""

import uuid

import pytest
from django.db import IntegrityError, transaction

from apps.documents.models import (
    Document,
    DocumentMembership,
    DocumentRevision,
    DocumentState,
    Role,
)
from apps.documents.services import create_document

pytestmark = pytest.mark.django_db


def test_create_document_creates_state_at_version_zero_and_owner_membership(accounts):
    doc = create_document(accounts["alice"].user, "Notes", "draft")
    state = DocumentState.objects.get(document=doc)
    assert (state.content, state.version) == ("draft", 0)
    membership = DocumentMembership.objects.get(document=doc)
    assert (membership.user_id, membership.role) == (accounts["alice"].id, Role.OWNER)


def test_membership_is_unique_per_user_and_document(document, accounts):
    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentMembership.objects.create(document=document, user=accounts["bob"].user, role=Role.VIEWER)


def test_membership_role_check_constraint(document, accounts):
    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentMembership.objects.create(document=document, user=accounts["mallory"].user, role="admin")


def _revision(document, author, version, mutation_id=None):
    return DocumentRevision.objects.create(
        document=document, version=version, author=author, client_id=uuid.uuid4(),
        mutation_id=mutation_id or uuid.uuid4(), operations=[{"kind": "insert", "pos": 0, "text": "x"}],
    )


def test_revision_version_is_unique_per_document(document, accounts):
    _revision(document, accounts["alice"].user, 1)
    with pytest.raises(IntegrityError), transaction.atomic():
        _revision(document, accounts["bob"].user, 1)


def test_revision_mutation_id_is_unique_per_document(document, accounts):
    mid = uuid.uuid4()
    _revision(document, accounts["alice"].user, 1, mid)
    with pytest.raises(IntegrityError), transaction.atomic():
        _revision(document, accounts["alice"].user, 2, mid)


def test_state_version_cannot_be_negative(document):
    with pytest.raises(IntegrityError), transaction.atomic():
        DocumentState.objects.filter(document=document).update(version=-1)


def test_deleting_document_cascades_to_state_memberships_and_revisions(document, accounts):
    _revision(document, accounts["alice"].user, 1)
    document_id = document.id
    Document.objects.filter(pk=document_id).delete()
    assert not DocumentState.objects.filter(document_id=document_id).exists()
    assert not DocumentMembership.objects.filter(document_id=document_id).exists()
    assert not DocumentRevision.objects.filter(document_id=document_id).exists()
