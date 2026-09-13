"""IRC — a nick on the network the customer chooses, shipped official (D-032).

The transport lives in `api/channels/irc.py` and runs as the lifespan's supervised
client connections. What registers here is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context

import logging

logger = logging.getLogger("api.extensions.irc")

MANIFEST = {
    "slug": "irc",
    "name": "IRC",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Answers private messages to your IRC nick, and channel lines "
    "addressed to it.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The connection does the work; this subscription proves the wiring."""
    logger.debug("irc saw a message", extra={"conversation_id": payload.get("conversation_id")})
