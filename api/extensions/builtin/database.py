"""Storage, declared as a system application because the catalogue lists it as one."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.extensions.registry import Context


MANIFEST = {
    "slug": "database",
    "name": "Database",
    "version": "0.1.0",
    "origin": "official",
    "category": "system",
    "description": "Stores conversations, transcripts and settings.",
    "scopes": ["conversations.read", "messages.read"],
    "hooks": [],
}


def register(context: "Context") -> None:
    """Storage is not event-driven; it is what the events are written into."""
