"""Document lifecycle operations shared by the REST API, the seed command and tests."""

from __future__ import annotations

from django.db import transaction

from .models import Document, DocumentMembership, DocumentState, Role


@transaction.atomic
def create_document(user, title: str, content: str = "") -> Document:
    document = Document.objects.create(title=title, created_by=user)
    DocumentState.objects.create(document=document, content=content, version=0)
    DocumentMembership.objects.create(document=document, user=user, role=Role.OWNER)
    return document


def add_member(document: Document, user, role: str) -> DocumentMembership:
    membership, _ = DocumentMembership.objects.update_or_create(
        document=document, user=user, defaults={"role": role}
    )
    return membership


def get_role(user_id: int, document_id) -> str | None:
    return (
        DocumentMembership.objects.filter(user_id=user_id, document_id=document_id)
        .values_list("role", flat=True)
        .first()
    )
