"""What an extension declares about itself — the contract of D-031.

A manifest is the whole of what the core knows before any of an extension's code runs.
It is validated on load and stored in `apps.manifest`, so the catalogue screen can list
something the installation has never executed.

**Extensions run in this process** (decided for v1). That buys simplicity and speed and
costs isolation: a module that raises at import takes the server with it, and one that
blocks the event loop stalls every request. The engine's job is therefore to fail
*narrowly* — a bad extension must be refused at load, and a bad hook must not take down
the thing that emitted the event. Where that is impossible, it is said plainly rather
than papered over.

`scopes` is the honest part of that trade. In this process an extension can import
whatever it likes; declaring a scope does not physically prevent anything. What it does
is make the claim explicit and reviewable: the catalogue shows what an extension asked
for, installing is a decision made against that list, and an extension that reaches
beyond it is committing a visible breach rather than an invisible one. That is worth
having now, and it is what a future out-of-process runtime would enforce for real.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Categories the `apps` screen already groups by.
CATEGORIES = (
    "system",
    "channels",
    "calendars",
    "tools",
    "notifications",
    "pbx",
    "sip",
    "health",
    "property",
    "hospitality",
    "accounting",
    "beauty",
    "analytics",
)

# What an extension may ask for. Deliberately coarse: a long list of fine-grained
# permissions nobody reads is worse than a short one somebody does.
SCOPES = (
    "conversations.read",
    "conversations.write",
    "messages.read",
    "messages.write",
    "channels.manage",
    "contacts.read",
    "contacts.write",
    "settings.read",
    "settings.write",
    "notifications.raise",
    "http.outbound",
)

_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


class InvalidManifest(ValueError):
    """The manifest is malformed. Raised at load, never at request time."""


@dataclass(frozen=True)
class Manifest:
    """One extension's declaration."""

    slug: str
    name: str
    version: str
    origin: str
    category: str
    description: str = ""
    scopes: tuple[str, ...] = ()
    # Event names this extension subscribes to. Declared as well as registered, so the
    # catalogue can say what an extension reacts to without importing it.
    hooks: tuple[str, ...] = ()
    # Where its own tables live, if it has any. A namespace rather than free rein:
    # `telegram_` prefixes its tables, so an extension can never migrate a core table
    # and two extensions cannot collide on a name.
    migration_prefix: str | None = None
    ui_slots: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        """The shape stored in `apps.manifest`."""
        return {
            "slug": self.slug,
            "name": self.name,
            "version": self.version,
            "origin": self.origin,
            "category": self.category,
            "description": self.description,
            "scopes": list(self.scopes),
            "hooks": list(self.hooks),
            "migration_prefix": self.migration_prefix,
            "ui_slots": list(self.ui_slots),
        }


def parse(raw: dict[str, Any]) -> Manifest:
    """Validate a declaration and return it, or raise `InvalidManifest`.

    Every failure names the field and what was expected. A manifest is usually written
    by somebody who is not us, and "invalid manifest" as the whole message is a message
    that costs an hour.
    """
    from api.models.extensions import ORIGINS

    def need(key: str) -> Any:
        if key not in raw or raw[key] in (None, ""):
            raise InvalidManifest(f"manifest is missing {key!r}")
        return raw[key]

    slug = str(need("slug"))
    if not _SLUG.match(slug):
        raise InvalidManifest(
            f"slug {slug!r} must be lowercase letters, digits and underscores, "
            "starting with a letter (2-64 characters)"
        )

    version = str(need("version"))
    if not _VERSION.match(version):
        raise InvalidManifest(f"version {version!r} must look like 1.2.3")

    origin = str(need("origin"))
    if origin not in ORIGINS:
        raise InvalidManifest(f"origin {origin!r} must be one of {list(ORIGINS)}")

    category = str(need("category"))
    if category not in CATEGORIES:
        raise InvalidManifest(f"category {category!r} must be one of {list(CATEGORIES)}")

    scopes = tuple(raw.get("scopes") or ())
    unknown = [scope for scope in scopes if scope not in SCOPES]
    if unknown:
        raise InvalidManifest(
            f"unknown scope(s) {unknown}: an extension may only ask for {list(SCOPES)}"
        )

    prefix = raw.get("migration_prefix")
    if prefix is not None:
        prefix = str(prefix)
        if not prefix.endswith("_") or not _SLUG.match(prefix.rstrip("_")):
            raise InvalidManifest(
                f"migration_prefix {prefix!r} must be a slug ending in an underscore, "
                "so an extension's tables can never be mistaken for a core table"
            )

    return Manifest(
        slug=slug,
        name=str(need("name")),
        version=version,
        origin=origin,
        category=category,
        description=str(raw.get("description") or ""),
        scopes=scopes,
        hooks=tuple(raw.get("hooks") or ()),
        migration_prefix=prefix,
        ui_slots=tuple(raw.get("ui_slots") or ()),
    )
