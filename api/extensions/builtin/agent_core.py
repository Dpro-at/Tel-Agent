"""The conversation engine, declared as the application the catalogue already lists.

It subscribes to nothing yet: the loop that will react to `message.received` arrives
with Epic F. The manifest exists now so the catalogue is truthful from the first boot
and so the core is subject to the same validation as anything else.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.extensions.registry import Context


MANIFEST = {
    "slug": "agent_core",
    "name": "Agent core",
    "version": "0.1.0",
    "origin": "official",
    "category": "system",
    "description": "Understands what a customer wants and decides what to say back.",
    "scopes": [
        "conversations.read",
        "conversations.write",
        "messages.read",
        "messages.write",
    ],
    "hooks": [],
}


def register(context: "Context") -> None:
    """Nothing to subscribe yet. Epic F attaches the loop to `message.received`."""
