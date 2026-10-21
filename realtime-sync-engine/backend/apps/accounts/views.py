"""Token auth endpoints. Tokens (not cookies) authenticate both REST and WebSocket
requests, which keeps cross-site WebSocket hijacking off the table."""

from django.contrib.auth import authenticate, get_user_model
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .serializers import CredentialsSerializer, UserSerializer

User = get_user_model()


def _token_response(user, http_status=status.HTTP_200_OK):
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": token.key, "user": UserSerializer(user).data}, status=http_status)


@api_view(["POST"])
@permission_classes([AllowAny])
def register(request):
    data = CredentialsSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    username = data.validated_data["username"]
    if User.objects.filter(username__iexact=username).exists():
        return Response({"detail": "That username is taken."}, status=status.HTTP_409_CONFLICT)
    user = User.objects.create_user(username=username, password=data.validated_data["password"])
    return _token_response(user, status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([AllowAny])
def login(request):
    data = CredentialsSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    user = authenticate(
        username=data.validated_data["username"], password=data.validated_data["password"]
    )
    if user is None:
        return Response(
            {"detail": "Username or password is incorrect."}, status=status.HTTP_401_UNAUTHORIZED
        )
    return _token_response(user)


@api_view(["GET"])
def me(request):
    return Response(UserSerializer(request.user).data)
