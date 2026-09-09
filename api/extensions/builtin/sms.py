"""SMS — texts to the business's own number, shipped official (D-032).

The transport lives in `api/channels/sms.py` and receives through the generic public
door; nothing is started here. What registers is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.sms")

MANIFEST = {
    "slug": "sms",
    "name": "SMS",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Texts to a number in your own messaging account are answered "
    "like any other conversation.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The door does the work; this subscription proves the wiring."""
    logger.debug("sms saw a message", extra={"conversation_id": payload.get("conversation_id")})
