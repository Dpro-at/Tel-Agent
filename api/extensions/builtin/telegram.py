"""Telegram — the first platform channel, shipped as an official application (D-032).

The transport itself lives in `api/channels/telegram.py` and runs as the lifespan's
polling loop rather than as a hook handler: a long poll is a loop's shape, not an
event's. What registers here is the application — so the apps screen lists it, the
catalogue records it, and the contract §B13 puts every channel behind is the one this
channel actually stands on.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.telegram")

MANIFEST = {
    "slug": "telegram",
    "name": "Telegram",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "A bot from your own @BotFather answers on Telegram. No review queue.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The transport loop does the work; this subscription proves the wiring.

    Logged rather than passing silently, for the reason web_chat's gives: a
    subscription that does nothing and says nothing is indistinguishable from one
    that was never registered.
    """
    logger.debug(
        "telegram saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
