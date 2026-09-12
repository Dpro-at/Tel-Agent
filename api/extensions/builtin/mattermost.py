"""Mattermost — a bot on the WebSocket gateway, shipped official (D-032).

The transport lives in `api/channels/mattermost.py` and runs as the lifespan's
supervised gateway connections. What registers here is the application, per §B13's
contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context

import logging

logger = logging.getLogger("api.extensions.mattermost")

MANIFEST = {
    "slug": "mattermost",
    "name": "Mattermost",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Answers customers who reach your Mattermost bot in DMs or mentions.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The gateway loop does the work; this subscription proves the wiring."""
    logger.debug(
        "mattermost saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
