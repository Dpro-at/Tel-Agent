"""Instagram — a Business account answering its DMs, shipped official (D-032).

The transport is shared with Messenger in `api/channels/meta_chat.py`: an Instagram
Business account is linked to a Facebook page and messaged through that page's
token. What registers here is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.instagram")

MANIFEST = {
    "slug": "instagram",
    "name": "Instagram",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Your own Meta application answers your Instagram DMs.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The webhook and its reply task do the work; this subscription proves the wiring."""
    logger.debug(
        "instagram saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
