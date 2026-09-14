"""Viber — the customer's own bot account, shipped official (D-032).

The transport lives in `api/channels/viber.py` and receives through the generic public
door; nothing is started here. What registers is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context

import logging

logger = logging.getLogger("api.extensions.viber")

MANIFEST = {
    "slug": "viber",
    "name": "Viber",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Answers people who message your Viber bot account.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The door does the work; this subscription proves the wiring."""
    logger.debug(
        "viber saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
