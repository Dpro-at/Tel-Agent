"""Email — the third no-platform channel, shipped as an official application (D-032).

The transport lives in `api/channels/email.py` and runs as the lifespan's polling
loop; what registers here is the application, so the apps screen and the catalogue
list it on the same contract every channel stands on (§B13).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.email")

MANIFEST = {
    "slug": "email",
    "name": "Email",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "An IMAP/SMTP mailbox you already own answers your mail.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """The transport loop does the work; this subscription proves the wiring."""
    logger.debug(
        "email saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
