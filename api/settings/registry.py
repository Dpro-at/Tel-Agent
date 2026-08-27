"""What a setting is: its scope, its type, its default, and whether it is a secret.

Declared once, here. A key that is not in this registry cannot be written — an
unknown key is a typo, and a store that accepts typos accumulates settings nobody
reads and defaults nobody notices are still in force.

The declaration also carries the boundary §B9.2 draws. `SMTP_PASSWORD` is a secret and
lives encrypted; `SMTP_HOST` is not. What never appears here is anything from the
*installation* half of that split — `DATABASE_URL`, `ENCRYPTION_KEY`, the LiveKit keys.
Those stay in `.env` and only there, because a settings screen that could rewrite the
key which decrypts this very table is a screen that can lock an installation out of
its own data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Scope = Literal["installation", "workspace"]
Kind = Literal["string", "integer", "boolean"]


@dataclass(frozen=True)
class Definition:
    key: str
    scope: Scope
    kind: Kind = "string"
    default: Any = None
    secret: bool = False
    description: str = ""


def _define(*definitions: Definition) -> dict[str, Definition]:
    return {definition.key: definition for definition in definitions}


# The mail server, moved out of `.env` because the forgot-password screen says in its
# own copy: "Configure a mail server in Settings". It could not, until now.
REGISTRY: dict[str, Definition] = _define(
    Definition("smtp.host", "installation", description="Hostname of the mail server."),
    Definition("smtp.port", "installation", "integer", 587, description="Usually 587."),
    Definition("smtp.username", "installation", description="Leave empty for no auth."),
    Definition("smtp.password", "installation", secret=True, description="Stored encrypted."),
    Definition("smtp.from", "installation", description="The From address on outgoing mail."),
    Definition("smtp.use_tls", "installation", "boolean", True, description="STARTTLS."),
    Definition("smtp.use_ssl", "installation", "boolean", False, description="Implicit TLS."),
    # Per workspace, because two businesses on one installation answer differently.
    Definition(
        "recording.announce",
        "workspace",
        "boolean",
        True,
        description="Announce that the call is recorded. Austria requires both parties "
        "to be aware, so this defaults on and turning it off is a decision.",
    ),
)


class UnknownSetting(KeyError):
    """A key that is not declared. Almost always a typo."""

    def __init__(self, key: str) -> None:
        super().__init__(
            f"{key!r} is not a declared setting. Add it to api/settings/registry.py "
            "with its scope and whether it is a secret."
        )


def definition(key: str) -> Definition:
    try:
        return REGISTRY[key]
    except KeyError:
        raise UnknownSetting(key) from None


def coerce(definition: Definition, raw: str) -> Any:
    """Turn a stored string into the declared type.

    Everything is stored as text - one column shape for every setting - so the type
    lives in the declaration and is applied on the way out.
    """
    if definition.kind == "integer":
        return int(raw)
    if definition.kind == "boolean":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return raw


def serialise(definition: Definition, value: Any) -> str:
    if definition.kind == "boolean":
        return "true" if value else "false"
    return str(value)
