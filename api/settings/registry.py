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
    # The catalogue - A6.11. One currency per workspace, not one per service: a
    # business does not price half its work in euros and half in francs, and a column
    # that could would have to be checked against itself on every read. ISO 4217, so
    # the interface can format an amount without a table of its own.
    Definition(
        "catalogue.currency",
        "workspace",
        default="EUR",
        description="ISO 4217 code the catalogue's prices are in, for example EUR or CHF.",
    ),
    # The model that answers - §B9.2, which puts LLM keys in the encrypted column and
    # not in `.env`. The environment still configures a model, because the agent runs
    # without an API server at Milestone 11 and an installer's `.env` must keep
    # working; what changed is which one wins. `api.llm.resolve` prefers these, so a
    # key saved on the screen takes effect on the next turn rather than at the next
    # restart.
    #
    # **Installation scope, deliberately.** Two workspaces on one machine share a model
    # today, the way they share a mail server: a key is bought once and metered once.
    # The store already resolves workspace-then-installation, so the day one business
    # wants its own model this is a one-word change here and no migration.
    Definition(
        "llm.provider",
        "installation",
        description="Which kind of endpoint answers. Empty means no model is "
        "connected, which is a supported state: the agent still takes messages.",
    ),
    Definition(
        "llm.model",
        "installation",
        description="The model name the endpoint expects, for example gpt-4o-mini.",
    ),
    Definition(
        "llm.api_key",
        "installation",
        secret=True,
        description="Stored encrypted, and never returned in full. A local model that "
        "wants no key still needs something here - most such servers accept any value.",
    ),
    Definition(
        "llm.base_url",
        "installation",
        description="Where the endpoint lives. Empty means the OpenAI-compatible "
        "default; point it at a gateway or a model on your own machine to use those.",
    ),
    # The generic HTTP tool - §B7's escape hatch, Milestone 5. The allowlist is the
    # whole of its safety: the model chooses the URL, and without this list it could
    # choose this installation's own loopback. Per workspace, because integrations
    # are the business's own.
    Definition(
        "http_tool.allowed_urls",
        "workspace",
        description="Comma-separated URL prefixes the agent's HTTP tool may call, "
        "for example https://orders.example.com/api. Empty means the tool refuses "
        "everything.",
    ),
    Definition("smtp.host", "installation", description="Hostname of the mail server."),
    Definition("smtp.port", "installation", "integer", 587, description="Usually 587."),
    Definition("smtp.username", "installation", description="Leave empty for no auth."),
    Definition("smtp.password", "installation", secret=True, description="Stored encrypted."),
    Definition("smtp.from", "installation", description="The From address on outgoing mail."),
    Definition("smtp.use_tls", "installation", "boolean", True, description="STARTTLS."),
    Definition("smtp.use_ssl", "installation", "boolean", False, description="Implicit TLS."),
    # Backup - P7. The target is a directory this process can write to: a mounted
    # network share or a USB disk, which are the same thing to this code. Empty means
    # no target chosen, which is a real state with its own screen and not a default to
    # be filled in - writing backups next to the database by default would put the
    # only copy on the disk whose failure is the thing being insured against.
    Definition(
        "backup.target_path",
        "installation",
        description="Directory backups are written to. A mounted network share or a "
        "USB disk. Empty means no backups are taken.",
    ),
    Definition(
        "backup.include_recordings",
        "installation",
        "boolean",
        False,
        description="Include call audio. Roughly 60x larger archives; excluding it "
        "leaves restores with transcripts but no audio to prove what was said.",
    ),
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
