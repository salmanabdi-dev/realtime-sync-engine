from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from ..models import DocumentMembership
from ..permissions import can_manage_members, require_membership
from ..services import add_member, create_document
from .serializers import DocumentCreateSerializer, MemberAddSerializer, document_summary

User = get_user_model()


@api_view(["GET", "POST"])
def documents(request):
    if request.method == "POST":
        data = DocumentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        doc = create_document(request.user, data.validated_data["title"], data.validated_data["content"])
        membership = DocumentMembership.objects.select_related(
            "document", "document__state", "document__created_by"
        ).get(document=doc, user=request.user)
        return Response(document_summary(membership), status=status.HTTP_201_CREATED)

    memberships = (
        DocumentMembership.objects.filter(user=request.user)
        .select_related("document", "document__state", "document__created_by")
        .order_by("-document__created_at")
    )
    return Response([document_summary(m) for m in memberships])


@api_view(["GET"])
def document_detail(request, document_id):
    membership = require_membership(request.user, document_id)
    doc = membership.document
    members = DocumentMembership.objects.filter(document=doc).select_related("user").order_by("user__username")
    return Response(
        {
            **document_summary(membership),
            "content": doc.state.content,
            "members": [
                {"user_id": m.user_id, "username": m.user.username, "role": m.role} for m in members
            ],
        }
    )


@api_view(["POST"])
def document_members(request, document_id):
    membership = require_membership(request.user, document_id)
    if not can_manage_members(membership.role):
        return Response({"detail": "Only the owner can share this document."}, status=status.HTTP_403_FORBIDDEN)
    data = MemberAddSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    user = User.objects.filter(username__iexact=data.validated_data["username"]).first()
    if user is None:
        return Response({"detail": "No user with that username."}, status=status.HTTP_404_NOT_FOUND)
    if user.pk == request.user.pk:
        return Response({"detail": "You already own this document."}, status=status.HTTP_400_BAD_REQUEST)
    m = add_member(membership.document, user, data.validated_data["role"])
    return Response({"user_id": user.pk, "username": user.username, "role": m.role}, status=status.HTTP_201_CREATED)
