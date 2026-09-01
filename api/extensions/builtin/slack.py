"""Slack — Socket Mode, shipped official (D-032).

The transport lives in `api/channels/slack.py` and runs as the lifespan's supervised
socket connections. What registers here is the application, per §B13's contract —
and §B13's own line about Slack: the shared-channel customer is the case, an
internal workspace is not.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.slack")

MANIFEST = {
    "slug": "slack",
    "name": "Slack",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Your own Slack app answers DMs and mentions over Socket Mode.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The socket loop does the work; this subscription proves the wiring."""
    logger.debug(
        "slack saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
