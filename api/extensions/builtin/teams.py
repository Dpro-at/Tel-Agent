"""Microsoft Teams — a bot registration the customer owns, shipped official (D-032).

The transport lives in `api/channels/teams.py` and receives through the generic public
door; nothing is started here. What registers is the application, per §B13's contract.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.teams")

MANIFEST = {
    "slug": "teams",
    "name": "Microsoft Teams",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "Answers customers who reach your Teams bot from outside your "
    "organisation. Internal chat is not a channel.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The door does the work; this subscription proves the wiring."""
    logger.debug(
        "teams saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
