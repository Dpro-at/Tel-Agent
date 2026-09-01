"""WhatsApp — the first platform channel with a review queue, shipped official (D-032).

The transport lives in `api/channels/whatsapp.py`; inbound arrives on the public
webhook in `api/routes/whatsapp_channel.py`, because the Cloud API offers nothing to
poll. What registers here is the application, so the apps screen and the catalogue
list it on the contract every channel stands on (§B13).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.whatsapp")

MANIFEST = {
    "slug": "whatsapp",
    "name": "WhatsApp",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Your own Meta application answers on WhatsApp.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The webhook and its reply task do the work; this subscription proves the wiring."""
    logger.debug(
        "whatsapp saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
