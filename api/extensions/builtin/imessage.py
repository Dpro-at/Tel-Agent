"""iMessage — the Apple ID on the customer's own Mac, through a bridge, shipped official.

The transport lives in `api/channels/imessage.py` and runs as the lifespan's supervised
poll connections. What registers here is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context

import logging

logger = logging.getLogger("api.extensions.imessage")

MANIFEST = {
    "slug": "imessage",
    "name": "iMessage",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Answers people who message the Apple ID on your Mac, and group chats "
    "that address it by name, through a bridge on your own network.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The poll loop does the work; this subscription proves the wiring."""
    logger.debug(
        "imessage saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
