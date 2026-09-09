"""What a channel declares about itself, so that nobody has to write its card.

Eight channels justified eight hand-written React cards and eight hand-written route
files. Twenty-five do not (D-044). Everything that differs between one channel's
settings card and the next is words and field names, and both of those are data — so
each transport declares a `Setup` next to itself, one generic route family serves it
and one generic card draws it.

The descriptor holds no values, only the shape of them. A credential lives encrypted
in `Channel.credentials_encrypted` and is never returned; a field declared here is the
label above the box the operator types it into.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    """One box on the card.

    `secret` defaults to **True** on purpose. A field whose secrecy was forgotten is
    returned to the browser in full and logged in an audit detail; a field wrongly
    marked secret is merely inconvenient. The safe default is the one that costs
    nothing when it is wrong.

    `multiline` is for the credentials platforms hand over as a file rather than a
    line — a service-account key is JSON, and a single-line input turns pasting one
    into a guessing game.
    """

    name: str
    label: str
    secret: bool = True
    required: bool = True
    help: str = ""
    placeholder: str = ""
    multiline: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
            "help": self.help,
            "placeholder": self.placeholder,
            "multiline": self.multiline,
        }


@dataclass(frozen=True)
class Setup:
    """One channel's whole card, declared once.

    `verified_live=False` prints "built against the published API, not yet verified
    with a live account" on the card. Six of this wave's platforms cannot be exercised
    end to end from here, and D-044 puts that sentence where the operator reads it
    rather than in a changelog nobody opens.
    """

    kind: str
    title: str
    note: str
    guide_url: str
    fields: tuple[Field, ...]
    verified_live: bool = True

    def public(self) -> dict[str, Any]:
        """The JSON the card draws from.

        Field metadata only. There is nowhere in this payload for a value to travel,
        which is what makes "the secret never comes back" a property of the shape
        rather than of every route that returns it.
        """
        return {
            "kind": self.kind,
            "title": self.title,
            "note": self.note,
            "guide_url": self.guide_url,
            "verified_live": self.verified_live,
            "fields": [field.public() for field in self.fields],
        }

    def field(self, name: str) -> Field | None:
        for field in self.fields:
            if field.name == name:
                return field
        return None

    def secret_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.secret)

    def shown_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if not field.secret)

    def required_names(self) -> tuple[str, ...]:
        """Every field that has to be filled in before the channel can be switched on.

        Secret or shown, because a channel can have no secrets at all — a transport
        that dials out to a companion process on the same machine needs an address and
        nothing else — and it must still be possible to switch it on.
        """
        return tuple(field.name for field in self.fields if field.required)

    def required_secrets(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.required and field.secret)
