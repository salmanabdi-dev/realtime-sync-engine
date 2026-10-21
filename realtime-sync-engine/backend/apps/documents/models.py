"""Normalized relational model for collaborative documents.

    auth_user 1──* document_memberships *──1 documents
    documents 1──1 document_states          (hot row: current content + version)
    documents 1──* document_revisions       (append-only accepted-mutation log)

`document_states.content` is the one deliberate materialization: it could be
rebuilt by replaying revisions, but storing it makes opening a document a single
primary-key read. It is only ever written in the same transaction as the
revision that produced it, so the two cannot drift apart.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Role(models.TextChoices):
    OWNER = "owner", "Owner"
    EDITOR = "editor", "Editor"
    VIEWER = "viewer", "Viewer"


WRITE_ROLES = frozenset({Role.OWNER, Role.EDITOR})


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    # Historical fact (who created it). Current ownership lives in memberships.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_documents"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="DocumentMembership", related_name="documents"
    )

    class Meta:
        db_table = "documents"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class DocumentMembership(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="document_memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_memberships"
        constraints = [
            models.UniqueConstraint(fields=["document", "user"], name="uniq_membership"),
            models.CheckConstraint(
                condition=Q(role__in=[r.value for r in Role]), name="membership_role_valid"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.role}@{self.document_id}"


class DocumentState(models.Model):
    document = models.OneToOneField(
        Document, on_delete=models.CASCADE, primary_key=True, related_name="state"
    )
    content = models.TextField(default="", blank=True)
    version = models.BigIntegerField(default=0)

    class Meta:
        db_table = "document_states"
        constraints = [models.CheckConstraint(condition=Q(version__gte=0), name="state_version_nonneg")]


class DocumentRevision(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="revisions")
    version = models.BigIntegerField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="revisions"
    )
    client_id = models.UUIDField()
    mutation_id = models.UUIDField()
    operations = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_revisions"
        ordering = ["version"]
        constraints = [
            # Backstop for optimistic concurrency: there can never be two version Ns.
            models.UniqueConstraint(fields=["document", "version"], name="uniq_revision_version"),
            # Idempotency: a retried mutation can never apply twice.
            models.UniqueConstraint(fields=["document", "mutation_id"], name="uniq_revision_mutation"),
            models.CheckConstraint(condition=Q(version__gt=0), name="revision_version_positive"),
        ]

    def as_wire(self) -> dict:
        return {
            "version": self.version,
            "ops": self.operations,
            "author_id": self.author_id,
            "client_id": str(self.client_id),
            "mutation_id": str(self.mutation_id),
        }
