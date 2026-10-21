"""WebSocket token authentication: ws://host/ws/documents/<id>/?token=<key>"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def user_for_token(key: str):
    from rest_framework.authtoken.models import Token

    token = Token.objects.select_related("user").filter(key=key).first()
    if token is None or not token.user.is_active:
        return AnonymousUser()
    return token.user


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        params = parse_qs(scope.get("query_string", b"").decode())
        key = (params.get("token") or [None])[0]
        scope["user"] = await user_for_token(key) if key else AnonymousUser()
        return await super().__call__(scope, receive, send)
