"""Messenger — a Meta page answering its own inbox, shipped official (D-032).

The transport is shared with Instagram in `api/channels/meta_chat.py` — one product
family on Meta's side, one module here. What registers here is the application, so
the apps screen and the catalogue list it on the contract every channel stands on
(§B13).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.messenger")

MANIFEST = {
    "slug": "messenger",
    "name": "Messenger",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Your own Meta application answers your Facebook page's inbox.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The webhook and its reply task do the work; this subscription proves the wiring."""
    logger.debug(
        "messenger saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
