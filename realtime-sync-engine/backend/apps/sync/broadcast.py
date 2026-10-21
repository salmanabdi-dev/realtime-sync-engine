"""Group naming and fan-out helpers on top of the Redis pub/sub channel layer.

Each consumer subscribes to `doc.<uuid>`. group_send becomes one Redis PUBLISH
that every server process subscribed to that group receives; each process then
dispatches to its local consumers. The update payload is encoded to JSON once
here, not once per receiving socket.
"""

from __future__ import annotations

from .codec import dumps


def group_name(document_id) -> str:
    # Channels group names: ASCII alnum, '-', '_', '.', < 100 chars.
    return f"doc.{document_id}"


async def publish_update(channel_layer, document_id, payload: dict, origin: str) -> None:
    await channel_layer.group_send(
        group_name(document_id),
        {"type": "doc.update", "text": dumps(payload), "origin": origin},
    )


async def publish_presence(channel_layer, document_id, event: str, origin: str, *,
                           user_id: int, username: str, client_id: str) -> None:
    await channel_layer.group_send(
        group_name(document_id),
        {"type": "doc.presence", "event": event, "origin": origin, "user_id": user_id,
         "username": username, "client_id": client_id},
    )
