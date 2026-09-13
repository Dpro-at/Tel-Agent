"""Matrix — a bot account on the customer's own homeserver, shipped official (D-032).

The transport lives in `api/channels/matrix.py` and runs as the lifespan's supervised
sync connections. What registers here is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context

import logging

logger = logging.getLogger("api.extensions.matrix")

MANIFEST = {
    "slug": "matrix",
    "name": "Matrix",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Answers customers who reach your Matrix bot in direct chats or mentions. "
    "Unencrypted rooms only.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The sync loop does the work; this subscription proves the wiring."""
    logger.debug(
        "matrix saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
