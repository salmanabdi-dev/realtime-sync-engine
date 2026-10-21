from django.http import Http404

from .models import WRITE_ROLES, DocumentMembership, Role


def require_membership(user, document_id) -> DocumentMembership:
    """Return the caller's membership or 404 (never reveal that a document exists)."""
    membership = (
        DocumentMembership.objects.select_related("document", "document__state")
        .filter(user=user, document_id=document_id)
        .first()
    )
    if membership is None:
        raise Http404("Document not found.")
    return membership


def can_write(role: str | None) -> bool:
    return role in WRITE_ROLES


def can_manage_members(role: str | None) -> bool:
    return role == Role.OWNER
