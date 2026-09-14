"""Signal — a number on the REST bridge the customer runs, shipped official (D-032).

The transport lives in `api/channels/signal.py` and runs as the lifespan's supervised
receive connections. What registers here is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context

import logging

logger = logging.getLogger("api.extensions.signal")

MANIFEST = {
    "slug": "signal",
    "name": "Signal",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Answers people who message your Signal number, and group chats that "
    "mention it, through a bridge on your own network.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The receive loop does the work; this subscription proves the wiring."""
    logger.debug(
        "signal saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
