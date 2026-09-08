"""The registry every declarative channel joins, and what a member has to look like.

A channel built on this contract writes no routes of its own: it declares a `Setup`,
implements the module surface below, and registers here. `api/routes/generic_channel.py`
serves its card, `api/routes/public_channel.py` serves its door, and
`api/main.py` starts its loop.

**Where a channel's values live is not a detail.** Secret fields go into
`Channel.credentials_encrypted` as one JSON object — encrypted at rest, write-only,
never returned. Shown fields go into `settings_json` under `"fields"`, which is plain
and index-able, because the inbound hot path reads them on every message and
decrypting to answer "which account is this" would put a cipher on the door.
`credentials_of` is what hands a transport the two halves as one dict, so no transport
has to know the split.
"""

from __future__ import annotations

import json
import logging
from types import ModuleType
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels.setup import Setup
from api.models import Channel

logger = logging.getLogger("api.channels")


class ChannelRefused(Exception):
    """The platform rejected the credential, or the caller's proof of identity.

    Raised by `probe` and `send_text` when a platform says no, and by the shared
    verifiers in `api/channels/jwt.py`. The card turns it into one 502 naming the
    kind; the door turns it into the single 403 that every failure gets.
    """


class ChannelModule(Protocol):
    """What `api/channels/<kind>.py` exposes. The signatures are Discord's."""

    KIND: str
    SETUP: Setup
    INBOUND: Literal["dial_out", "door"]

    def make_client(self) -> httpx.AsyncClient: ...

    async def probe(self, client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
        """The identity the platform reports, or `ChannelRefused` — the test button."""

    async def send_text(
        self, client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
    ) -> None: ...

    def message_text(self, event: Any, identity: Any) -> str | None:
        """The answering policy: the text to reply to, or `None` to ignore the event."""

    async def ingest(self, db: DbSession, channel: Channel, event: Any) -> int | None:
        """Store one inbound line. `None` when it was a duplicate or not for us."""

    async def respond(self, sessionmaker: Any, channel_id: int, message_id: int) -> None: ...

    def schedule_reply(self, sessionmaker: Any, channel_id: int, message_id: int) -> None: ...

    # A dial-out module also has `async def loop(sessionmaker) -> None`, the supervisor
    # that keeps one connection per active channel. A door module instead has
    # `async def receive(db, channel, request) -> Response`.
    #
    # Either may also have `async def activate(client, credentials, webhook_url) -> None`
    # — what the platform has to be told once the operator switches the channel on.
    # The PUT route calls it after enabling, and a `ChannelRefused` from it leaves the
    # channel off: a channel the platform does not know about is not switched on, it is
    # merely marked so.


# Filled by `register`. Empty until the first declarative channel arrives; the routes
# and the health rollup read it rather than a hardcoded list, so adding a channel is
# adding a line here.
CHANNELS: dict[str, ModuleType] = {}


# The surface every declarative channel owes, whichever way it receives.
# `.claude/skills/channel-extension/SKILL.md` is where this list is written for humans;
# this tuple is the same list, enforced.
_REQUIRED_CALLABLES = (
    "make_client",
    "probe",
    "send_text",
    "message_text",
    "ingest",
    "respond",
    "schedule_reply",
)

# What each way of receiving adds to it.
_BY_INBOUND = {"dial_out": "loop", "door": "receive"}


def register(module: ModuleType) -> None:
    """Put one transport module on the registry, keyed by its own `KIND`.

    The surface is checked here rather than at the first request. A module missing
    `receive` is a door that answers 500 to a stranger, and a module missing `SETUP` is
    a settings card that cannot be drawn - both of them at runtime, in production,
    long after import. Registration is the one moment where the whole module is in
    hand, so it is where the contract is enforced.
    """
    missing = [name for name in ("KIND", "SETUP", "INBOUND") if not hasattr(module, name)]
    if missing:
        raise TypeError(f"{module.__name__} declares no {', '.join(missing)}")

    inbound = module.INBOUND
    if inbound not in _BY_INBOUND:
        raise TypeError(
            f"{module.__name__} declares INBOUND={inbound!r}; "
            f"it must be one of {', '.join(sorted(_BY_INBOUND))}"
        )

    wanted = (*_REQUIRED_CALLABLES, _BY_INBOUND[inbound])
    absent = [name for name in wanted if not callable(getattr(module, name, None))]
    if absent:
        raise TypeError(f"{module.__name__} has no callable {', '.join(absent)}")

    if not isinstance(module.SETUP, Setup):
        raise TypeError(f"{module.__name__}.SETUP is not a Setup descriptor")
    if module.SETUP.kind != module.KIND:
        raise TypeError(
            f"{module.__name__}.SETUP.kind is {module.SETUP.kind!r}, "
            f"but KIND is {module.KIND!r}"
        )

    kind = module.KIND
    if kind in CHANNELS and CHANNELS[kind] is not module:
        raise ValueError(f"two modules claim the channel kind {kind!r}")
    CHANNELS[kind] = module


def module_for(kind: str) -> ModuleType | None:
    return CHANNELS.get(kind)


def dial_out_modules() -> list[ModuleType]:
    """The modules with a supervisor loop, for `api/main.py` to start."""
    return [
        module
        for module in CHANNELS.values()
        if getattr(module, "INBOUND", "") == "dial_out" and hasattr(module, "loop")
    ]


def secrets_of(channel: Channel | None) -> dict[str, str]:
    """The decrypted credential object. `{}` when the channel has none stored."""
    if channel is None or not channel.credentials_encrypted:
        return {}
    try:
        stored = json.loads(channel.credentials_encrypted)
    except ValueError:
        logger.warning(
            "channel credentials are not a JSON object", extra={"channel_id": channel.id}
        )
        return {}
    if not isinstance(stored, dict):
        return {}
    return {str(name): str(value) for name, value in stored.items()}


def shown_of(channel: Channel | None) -> dict[str, str]:
    """The non-secret field values, from the plain settings column."""
    if channel is None:
        return {}
    stored = (channel.settings_json or {}).get("fields")
    if not isinstance(stored, dict):
        return {}
    return {str(name): str(value) for name, value in stored.items()}


def credentials_of(channel: Channel | None) -> dict[str, str]:
    """Every field the operator filled in, secret and shown, as one dict.

    What a transport is handed. The split between the two columns is this module's
    business and not the transport's - a channel asks for `credentials["account"]`
    without caring which half of the row it came out of.
    """
    return {**shown_of(channel), **secrets_of(channel)}


def store_secrets(channel: Channel, values: dict[str, str]) -> None:
    """Replace the encrypted credential object. Empty means: store nothing at all."""
    channel.credentials_encrypted = json.dumps(values, sort_keys=True) if values else None


def store_shown(channel: Channel, values: dict[str, str]) -> None:
    settings = dict(channel.settings_json or {})
    if values:
        settings["fields"] = values
    else:
        settings.pop("fields", None)
    channel.settings_json = settings


def missing_fields(channel: Channel, setup: Setup) -> tuple[str, ...]:
    """The required fields that are still empty — what holds a channel switched off."""
    filled = credentials_of(channel)
    return tuple(name for name in setup.required_names() if not filled.get(name))
