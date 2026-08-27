"""Web chat — the first official application built on the contract.

D-027 records this as the mitigation for building the foundations before the chat: the
contract is proven by use rather than by inspection. The manifest and its subscription
land now; the handler body is Epic F's work, and it is deliberately a no-op rather than
absent, so the wiring is exercised from the first boot.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.extensions.registry import Context


import logging

logger = logging.getLogger("api.extensions.web_chat")

MANIFEST = {
    "slug": "web_chat",
    "name": "Web chat",
    "version": "0.1.0",
    "origin": "official",
    "category": "channels",
    "description": "A chat bubble on your own site. No account anywhere.",
    "scopes": ["conversations.write", "messages.read", "messages.write"],
    "hooks": ["message.received"],
    "ui_slots": ["conversation.detail"],
}


def register(context: "Context") -> None:
    context.on("message.received", _on_message)


async def _on_message(**payload: Any) -> None:
    """Epic F replaces this with the streaming loop.

    It logs rather than passing silently: a subscription that does nothing and says
    nothing is indistinguishable from one that was never registered.
    """
    logger.debug(
        "web_chat saw a message", extra={"conversation_id": payload.get("conversation_id")}
    )
