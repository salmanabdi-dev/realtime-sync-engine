from rest_framework import serializers

from ..models import Role


class DocumentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    content = serializers.CharField(required=False, allow_blank=True, default="", max_length=1_000_000)


class MemberAddSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    role = serializers.ChoiceField(choices=Role.choices)


def document_summary(membership) -> dict:
    doc = membership.document
    return {
        "id": str(doc.id),
        "title": doc.title,
        "role": membership.role,
        "version": doc.state.version,
        "created_by": doc.created_by.username,
        "created_at": doc.created_at.isoformat(),
    }
