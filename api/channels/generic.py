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

import asyncio
import json
import logging
import time
from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urlunsplit

import httpx
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import webhooks
from api.channels.setup import Setup
from api.conversations import position_ms
from api.db import session_scope
from api.models import Channel, Conversation, Message
from api.notifications import raise_notification

if TYPE_CHECKING:  # pragma: no cover - typing only
    from api.config import Settings

logger = logging.getLogger("api.channels")

# How much of a customer's words a notification may repeat.
PREVIEW_MAX = 80

# How many platform message ids one conversation remembers, per channel kind. A
# platform retries a delivery it thinks was not acknowledged, and it does not always
# retry the *latest* one - a single remembered id answers a repeat of the last message
# and lets an older repeat through, which is a duplicate line and a duplicate answer.
RECENT_IDS_KEPT = 32


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

    # Optional. The address `send_text` needs for one thread, when it is not the
    # conversation's own `external_id` - a room id, a thread key, a mailbox. A module
    # that answers where the customer wrote from declares nothing and
    # `reply_target_of` falls back to `external_id`.
    def reply_target(self, conversation: Conversation) -> str | None: ...

    # A dial-out module also has `async def loop(sessionmaker) -> None`, the supervisor
    # that keeps one connection per active channel. A door module instead has
    # `async def receive(db, channel, request) -> Response`.
    #
    # Either may also have `async def activate(client, credentials, webhook_url) -> None`
    # — what the platform has to be told once the operator switches the channel on.
    # The PUT route calls it after enabling, and a `ChannelRefused` from it leaves the
    # channel off: a channel the platform does not know about is not switched on, it is
    # merely marked so.


# Filled by `register`, and by `channels()` on the first read. The routes, the health
# rollup and `api/main.py` all reach it through `channels()` rather than through this
# dict, so adding a channel is adding a name to `_DECLARED` below.
CHANNELS: dict[str, ModuleType] = {}

# The channels that ship with the core, by module name. Imported on the first read of
# the registry and not at import of this module: a transport imports *this* module for
# `ChannelRefused`, so registering at our own import time makes the result depend on
# which of the two Python happened to import first - and importing the transport first
# left it half-built and unregistrable. Reading is the one moment where both modules
# are certainly whole.
_DECLARED: tuple[str, ...] = ("api.channels.sms",)


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


def channels() -> dict[str, ModuleType]:
    """The registry, with every declared channel on it — what a reader should call.

    Registration happens here rather than at import so that no import order can decide
    what this installation has. A kind already on the registry is left alone, which is
    what lets a test stand a fake in front of a real channel.
    """
    for name in _DECLARED:
        module = import_module(name)
        # Absent only while this module is itself being imported *by* that transport,
        # which no reader is inside; the next read registers it.
        kind = getattr(module, "KIND", None)
        if kind and CHANNELS.get(kind) is None:
            register(module)
    return CHANNELS


def module_for(kind: str) -> ModuleType | None:
    return channels().get(kind)


def dial_out_modules() -> list[ModuleType]:
    """The modules with a supervisor loop, for `api/main.py` to start."""
    return [
        module
        for module in channels().values()
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


# --- The public address ---------------------------------------------------------


def public_url_for(request: Request, settings: Settings) -> str:
    """The address the platform called, which is the address it signed.

    A platform that signs the URL as well as the body is signing the address it was
    configured with — the one the settings card printed. The application server behind
    a reverse proxy sees neither: it sees `http` and its own host. So the two have to
    come from one place, and `PUBLIC_BASE_URL` is that place. Set it and the card and
    the door agree by construction: both append the same path to the same base.

    **Unset, the forwarded headers are trusted here, and only here.** A client can send
    `X-Forwarded-Proto` and `X-Forwarded-Host` itself, so nothing else in this product
    reads them — `api/middleware/security_headers.py` refuses to infer TLS from one.
    They are safe at this door for one reason: they are not believed, they are *tested*.
    A wrong value produces a URL the signature does not match, and the delivery is
    refused. The cost of ignoring them is an installation behind a proxy where nothing
    ever verifies; the cost of reading them is nothing an attacker can spend, because
    forging the signature still needs the auth token they do not have.
    """
    url = request.url
    query = f"?{url.query}" if url.query else ""
    configured = (settings.public_base_url or "").strip().rstrip("/")
    if configured:
        return f"{configured}{url.path}{query}"

    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    forwarded_host = request.headers.get("X-Forwarded-Host")
    # The first value of each is the original client's, per the header's own convention.
    scheme = forwarded_proto.split(",")[0].strip() if forwarded_proto else url.scheme
    host = forwarded_host.split(",")[0].strip() if forwarded_host else url.netloc
    # Reconstructed rather than taken whole, so nothing but the scheme and the host can
    # be influenced from outside.
    return urlunsplit((scheme, host, url.path, url.query, ""))


# --- The conversation half, shared ----------------------------------------------


def seen_before(conversation: Conversation, kind: str, message_id: str) -> bool:
    """Has this platform message id arrived before? Records it either way.

    A retry is normal on every channel of this wave, and answering one twice is the
    failure. The last `RECENT_IDS_KEPT` ids are kept per kind in `state_json`, not one:
    a platform that retries an older delivery after a newer one has landed walks
    straight past a single remembered id.
    """
    if not message_id:
        return False
    state = dict((conversation.state_json or {}).get(kind) or {})
    recent = [str(seen) for seen in (state.get("recent_ids") or []) if seen]
    if message_id in recent:
        return True
    recent.append(message_id)
    state["recent_ids"] = recent[-RECENT_IDS_KEPT:]
    # Reassigned rather than mutated: a JSON column changed in place is not dirty.
    conversation.state_json = {**(conversation.state_json or {}), kind: state}
    return False


async def conversation_for(
    db: DbSession, channel: Channel, external_id: str, title: str | None = None
) -> tuple[Conversation, bool]:
    """This customer's open thread on this channel, and whether it was just started."""
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == external_id,
            Conversation.status == "open",
        )
    )
    if row is not None:
        return row, False
    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=external_id,
        handling="ai",
        status="open",
        # A conversation has no title column, so a name the platform gives a room lives
        # with the rest of that channel's own state.
        state_json={channel.kind: {"title": title}} if title else None,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row, True


async def store_line(
    db: DbSession,
    conversation: Conversation,
    role: str,
    text: str,
    *,
    language: str | None = None,
) -> Message:
    """One line into the transcript. `role` is the `speaker` column: caller or agent."""
    line = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        ts_ms=position_ms(conversation.started_at),
        speaker=role,
        text=text,
        language=language,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


async def announce(
    db: DbSession, channel: Channel, conversation: Conversation, message: Message, started: bool
) -> None:
    """The extension hooks for one inbound line — never at the cost of the line itself.

    A webhook that cannot be queued is logged and swallowed: the message is already
    stored, and losing it because an extension is misconfigured is the worse failure of
    the two.
    """
    try:
        if started:
            await webhooks.queue(
                db,
                workspace_id=channel.workspace_id,
                event="conversation.started",
                data={
                    "conversation": conversation.external_id,
                    "channel": channel.kind,
                    "started_at": conversation.started_at.isoformat()
                    if conversation.started_at
                    else None,
                },
            )
        await webhooks.queue(
            db,
            workspace_id=channel.workspace_id,
            event="message.received",
            data={
                "conversation": conversation.external_id,
                "message_id": message.id,
                "speaker": "caller",
                "text": message.text,
                "ts_ms": message.ts_ms,
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "could not queue webhooks for an inbound message",
            extra={"conversation_id": conversation.id, "kind": channel.kind},
        )


def preview(text: str) -> str:
    """One line of a customer's words, for a notification with room for one."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


def has_credentials(module: ModuleType, credentials: dict[str, str]) -> bool:
    """Whether every field this module's descriptor calls required is actually filled in.

    "Any credential at all" was the older test, and it passes for a channel holding one
    stale field out of three: the whole answer is generated, the platform refuses the
    send, and nothing is stored - work done and a health timing missed for a channel
    that was never configured. The descriptor already says which fields are required,
    so the guard asks it rather than guessing.

    A module with no descriptor - none ships, but the registry is open - keeps the old
    test, because there is nothing better to ask.
    """
    setup = getattr(module, "SETUP", None)
    if not isinstance(setup, Setup):
        return bool(credentials)
    return all(credentials.get(name) for name in setup.required_names())


def reply_target_of(module: ModuleType, conversation: Conversation) -> str | None:
    """Where a reply on this thread goes. `external_id` unless the module says otherwise."""
    own = getattr(module, "reply_target", None)
    if own is not None:
        return own(conversation)
    return conversation.external_id


# --- Generating and delivering an answer ----------------------------------------

# The replies still running, so a task is not collected mid-answer.
_REPLIES: set[asyncio.Task] = set()


def schedule_reply(
    sessionmaker: async_sessionmaker, module: ModuleType, channel_id: int, message_id: int
) -> None:
    """The answer as its own task, kept in a set so it cannot be collected mid-reply."""
    task = asyncio.create_task(respond(sessionmaker, module, channel_id, message_id))
    _REPLIES.add(task)
    task.add_done_callback(_REPLIES.discard)


async def deliver(
    module: ModuleType,
    client: httpx.AsyncClient,
    credentials: dict[str, str],
    target: str,
    text: str,
) -> None:
    """One answer out, as however many messages the platform's limit makes of it.

    A module that declares `split_text` is split here; one that does not sends the
    whole answer, because it has no limit worth splitting on or handles its own.
    """
    split = getattr(module, "split_text", None)
    limit = getattr(module, "MESSAGE_MAX", None)
    pieces = split(text, limit) if split is not None and limit else [text]
    for piece in pieces:
        if piece:
            await module.send_text(client, credentials, target, piece)


async def respond(
    sessionmaker: async_sessionmaker, module: ModuleType, channel_id: int, message_id: int
) -> None:
    """Generate and deliver the answer to one stored line — the channels' contract.

    Its own session, because the request that received the line is long gone. The
    takeover state is read at the start **and again immediately before the send**: a
    reply takes seconds to generate, and a person who takes the thread over during
    those seconds must not be talked over by an answer already in flight.

    **Delivery before storage.** The agent line is written only after the platform has
    accepted it. A refused send writes nothing — a transcript showing an answer the
    customer never received is worse than one showing none at all.
    """
    from agent.config import ConfigurationError
    from agent.reply import reply as generate_reply
    from agent.tools import TakenMessage
    from api import llm
    from api.agent_tools import toolset
    from api.channels import health
    from api.routes.public_chat import _history

    kind = str(getattr(module, "KIND", ""))
    async with session_scope(sessionmaker) as db:
        line = await db.scalar(select(Message).where(Message.id == message_id))
        if line is None:
            return
        conversation = await db.scalar(
            select(Conversation).where(Conversation.id == line.conversation_id)
        )
        channel = await db.scalar(select(Channel).where(Channel.id == channel_id))
        if conversation is None or channel is None or conversation.handling == "human":
            return
        credentials = credentials_of(channel)
        target = reply_target_of(module, conversation)
        if not target or not has_credentials(module, credentials):
            return

        async def took(taken: TakenMessage) -> None:
            await raise_notification(
                db,
                workspace_id=channel.workspace_id,
                category="review",
                message_key="message_taken",
                params={"name": taken.name, "reason": preview(taken.reason)},
                needs_decision=True,
                primary_action="open_conversation",
                action_payload={"conversation_id": conversation.id},
                conversation_id=conversation.id,
            )

        try:
            provider = await llm.resolve_provider(db)
        except ConfigurationError:
            logger.exception(
                "channel could not resolve a model",
                extra={"channel_id": channel_id, "kind": kind},
            )
            provider = None

        history = await _history(db, conversation, line)
        reply_started = time.perf_counter()
        # §B7's tools, bound to this conversation.
        tools = toolset(
            sessionmaker,
            workspace_id=channel.workspace_id,
            conversation_id=conversation.id,
        )

        pieces: list[str] = []
        async for chunk in generate_reply(
            line.text, provider=provider, history=history, on_message_taken=took, tools=tools
        ):
            pieces.append(chunk)
        whole = "".join(pieces)
        if not whole:
            return

        # Read again, now that generating is over: the thread may have been taken over
        # while the model was still writing.
        await db.refresh(conversation)
        if conversation.handling == "human":
            logger.info(
                "reply withheld, a person took the thread while it was being written",
                extra={"conversation_id": conversation.id, "kind": kind},
            )
            return

        try:
            async with module.make_client() as client:
                await deliver(module, client, credentials, target, whole)
        except (ChannelRefused, httpx.HTTPError) as error:
            logger.warning(
                "reply not delivered",
                extra={
                    "conversation_id": conversation.id,
                    "kind": kind,
                    "error": type(error).__name__,
                },
            )
            return
        await store_line(db, conversation, "agent", whole)

        # Rule 4: the whole journey, generation to delivery, measured per channel.
        health.note_reply(kind, channel.id, (time.perf_counter() - reply_started) * 1000)
