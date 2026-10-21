from channels.routing import URLRouter
from django.urls import path

from .consumers import DocumentConsumer
from .middleware import TokenAuthMiddleware

websocket_urlpatterns = [
    path("ws/documents/<uuid:document_id>/", DocumentConsumer.as_asgi()),
]


def build_websocket_application():
    """Token auth + routing. No cookie-origin check is needed because auth is a
    bearer token in the URL, not an ambient browser credential."""
    return TokenAuthMiddleware(URLRouter(websocket_urlpatterns))
