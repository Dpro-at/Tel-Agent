"""The Signal transport — a number on the REST bridge the customer runs beside us, §B13.

Signal publishes no server API for businesses. The customer registers or links a number
on a community REST bridge running next to Tel-Agent, on the same machine or the same
network, and gives the card two things: the bridge's address and the number. There is
no secret, because the bridge is the account; whoever can reach its address can send
as that number. Tel-Agent holds no shared application and exposes nothing.

**Dial-out, by long poll.** Inbound is the bridge's receive endpoint, called with a
timeout so it returns as soon as something arrives. Outbound is its send endpoint.
Receiving takes the messages off the bridge, so there is no position to keep. The
receive endpoint only answers a plain request in the bridge's normal and native modes.
In its JSON-RPC mode it wants a websocket, so the test button refuses that mode by name
rather than letting the loop fail quietly.

**A public address must use HTTPS.** The bridge sends and reads as the number, so a
plain-HTTP address is accepted only where it cannot cross the internet: a loopback or
private address, `localhost`, a hostname with no dot (a container on the same network),
or a local-only suffix. Anything else is refused before a request is made.

**Where the number answers is a policy.** A one-to-one message is always answered. In a
group it answers only when mentioned. Signal puts one placeholder character in the text
for each mention and describes it in UTF-16 positions, so the placeholder is cut by
those positions and an emoji before it does not shift the cut. The number's own
messages, and the copies Signal syncs from the owner's other devices, are never
answered. Signal has no bot accounts to tell apart, so the number's own is the one
automated sender that can be recognised.

**A backlog is not answered.** Signal holds messages for a number until something
receives them. A message that is already a day old when it arrives is dropped rather
than answered, so switching a long-idle number on does not reply to last month.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import sys
import time
from types import ModuleType
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import routing
from api.channels import generic
from api.channels.generic import ChannelRefused
from api.channels.setup import Field, Setup
from api.db import session_scope
from api.models import Channel, Conversation

logger = logging.getLogger("api.signal")

KIND = "signal"
INBOUND = "dial_out"

# Longer texts go out as an attachment the recipient has to open. Kept to what reads
# as a message.
MESSAGE_MAX = 2000

# How long one receive waits on the bridge for something to arrive, in seconds.
RECEIVE_TIMEOUT_SECONDS = 20

# How often the supervisor reconciles running connections against the channels table.
SUPERVISE_SECONDS = 15.0

# Older than this on arrival, and a message is a backlog rather than a conversation.
STALE_SECONDS = 24 * 60 * 60

# Where the bridge prefixes the ids of groups it can send to.
GROUP_PREFIX = "group."

# What a mention leaves in the text: one object replacement character per mention.
MENTION_PLACEHOLDER = "\ufffc"

# Hostnames that only resolve on a local network.
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa")

SETUP = Setup(
    kind=KIND,
    title="Signal",
    note="A Signal number on the REST bridge you run beside Tel-Agent answers direct "
    "messages, and group messages that mention it. Run the bridge in its normal or "
    "native mode. Tel-Agent collects messages from it and exposes nothing to the "
    "internet.",
    guide_url="https://support.signal.org/hc/en-us/articles/360007320551-Linked-Devices",
    fields=(
        Field(
            "base_url",
            "Bridge address",
            secret=False,
            help="Where Tel-Agent reaches the bridge. Plain http only on your own network.",
            placeholder="http://127.0.0.1:8080",
        ),
        Field(
            "number",
            "Signal number",
            secret=False,
            help="The number registered or linked on the bridge, with its country code.",
            placeholder="+43…",
        ),
    ),
    verified_live=False,
)


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


def make_client() -> httpx.AsyncClient:
    # Longer than one receive's own wait, or every quiet long poll would end as a timeout.
    return httpx.AsyncClient(timeout=httpx.Timeout(RECEIVE_TIMEOUT_SECONDS + 30))


def _is_local_host(host: str) -> bool:
    """Whether a plain-HTTP request to this host stays off the internet."""
    host = host.strip("[]").lower()
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES)
    return address.is_loopback or address.is_private or address.is_link_local


def base_url(credentials: dict[str, str]) -> str:
    """The bridge's address, refused when it would carry the number over plain HTTP."""
    raw = credentials.get("base_url", "").strip().rstrip("/")
    if not raw:
        raise ChannelRefused("base_url is required")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ChannelRefused("base_url must be an http or https address")
    if parts.scheme == "http" and not _is_local_host(parts.hostname):
        raise ChannelRefused("a bridge on a public address must use https")
    return raw


def _number(credentials: dict[str, str]) -> str:
    number = credentials.get("number", "").strip()
    if not number:
        raise ChannelRefused("number is required")
    return number


async def _call(
    client: httpx.AsyncClient,
    credentials: dict[str, str],
    method: str,
    path: str,
    **kwargs: Any,
) -> Any:
    """One bridge call. `ChannelRefused` when the bridge says no."""
    response = await client.request(method, f"{base_url(credentials)}{path}", **kwargs)
    if response.status_code >= 400:
        try:
            detail = str(response.json().get("error") or response.status_code)
        except (ValueError, AttributeError):
            detail = str(response.status_code)
        raise ChannelRefused(f"the bridge refused {path.split('?')[0]}: {detail[:120]}")
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError as error:
        raise ChannelRefused("the bridge did not answer with JSON") from error


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The test button: the bridge can receive, and it holds the configured number."""
    number = _number(credentials)
    about = await _call(client, credentials, "GET", "/v1/about")
    if isinstance(about, dict) and str(about.get("mode") or "") == "json-rpc":
        raise ChannelRefused(
            "the bridge runs in json-rpc mode; switch it to normal or native mode"
        )
    accounts = await _call(client, credentials, "GET", "/v1/accounts")
    if not isinstance(accounts, list) or number not in [str(entry) for entry in accounts]:
        raise ChannelRefused("this number is not registered on the bridge")
    return number


async def activate(
    client: httpx.AsyncClient, credentials: dict[str, str], webhook_url: str | None
) -> None:
    """Switching on checks the address, without calling the bridge.

    A bridge that is down for a moment should not stop anybody switching the channel
    on; the supervisor reports it and keeps trying. An address that would send over
    plain HTTP across the internet is never going to be right, so that is refused here.
    """
    base_url(credentials)
    _number(credentials)


def split_text(text: str, limit: int = MESSAGE_MAX) -> list[str]:
    return generic.split_on_words(text, limit)


def group_target(group_id: str) -> str:
    """The bridge's send address for a group, from the id a received message carries."""
    return GROUP_PREFIX + base64.b64encode(group_id.encode("utf-8")).decode("ascii")


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One text message to a person's number or id, or to a group."""
    await _call(
        client,
        credentials,
        "POST",
        "/v2/send",
        json={"message": text, "number": _number(credentials), "recipients": [target]},
    )


def _utf16_cut(text: str, start: int, length: int) -> str | None:
    """`text` without the span Signal describes in UTF-16 code units."""
    units = text.encode("utf-16-le")
    begin, end = start * 2, (start + length) * 2
    if start < 0 or length <= 0 or end > len(units):
        return None
    try:
        removed = units[begin:end].decode("utf-16-le")
        kept = (units[:begin] + units[end:]).decode("utf-16-le")
    except UnicodeDecodeError:
        # A span that splits a surrogate pair is not one this text can have.
        return None
    return kept if removed == MENTION_PLACEHOLDER * length else None


def _sender(envelope: dict[str, Any]) -> str:
    return str(
        envelope.get("sourceNumber")
        or envelope.get("sourceUuid")
        or envelope.get("source")
        or ""
    )


def message_text(event: Any, identity: Any) -> str | None:
    """The answering policy. `event` is one received item; `identity` the number."""
    if not isinstance(event, dict) or not isinstance(identity, dict):
        return None
    envelope = event.get("envelope")
    if not isinstance(envelope, dict):
        return None
    data = envelope.get("dataMessage")
    if not isinstance(data, dict):
        # Receipts, typing, calls, stories, edits, and the copies synced from the
        # owner's other devices all arrive without one.
        return None

    own = str(identity.get("number") or "")
    sender = _sender(envelope)
    if not own or not sender or own in (sender, envelope.get("sourceNumber")):
        return None

    text = str(data.get("message") or "")
    if not text.strip():
        return None
    group = data.get("groupInfo")
    if not isinstance(group, dict) or not group.get("groupId"):
        return text.strip()

    mentions = data.get("mentions") or []
    mine = [
        entry
        for entry in mentions
        if isinstance(entry, dict) and str(entry.get("number") or "") == own
    ]
    if not mine:
        return None
    # Cut from the end first, so an earlier span's position is still right.
    for entry in sorted(mine, key=lambda item: int(item.get("start", 0)), reverse=True):
        cut = _utf16_cut(text, int(entry.get("start", -1)), int(entry.get("length", 0)))
        if cut is not None:
            text = cut
    return " ".join(text.split()) or None


def reply_target(conversation: Conversation) -> str | None:
    """Where the answer goes: the chat the person last wrote in."""
    state = (conversation.state_json or {}).get(KIND) or {}
    return str(state.get("to") or "") or None


async def ingest(db: DbSession, channel: Channel, event: Any) -> int | None:
    """Store one received message. The stored line's id when a reply is due, else `None`.

    One conversation per person, as on every other chat channel: the record follows the
    customer, and the answer goes to the chat they last wrote in.
    """
    identity = event.get("own") if isinstance(event, dict) else None
    text = message_text(event, identity)
    if text is None:
        return None
    envelope = event["envelope"]
    data = envelope["dataMessage"]
    person = _sender(envelope)

    sent_ms = int(data.get("timestamp") or envelope.get("timestamp") or 0)
    if sent_ms and time.time() - sent_ms / 1000 > STALE_SECONDS:
        logger.info(
            "signal message too old to answer, dropped", extra={"channel_id": channel.id}
        )
        return None

    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[person])
    if decision.action == "block":
        logger.info(
            "signal message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await generic.conversation_for(db, channel, person)
    # A message has no id of its own on Signal: its sender and timestamp are what
    # every client uses to refer to it.
    if generic.seen_before(conversation, KIND, f"{person}:{sent_ms}"):
        logger.info(
            "signal message repeated, dropped", extra={"conversation_id": conversation.id}
        )
        return None

    group = data.get("groupInfo")
    to = (
        group_target(str(group["groupId"]))
        if isinstance(group, dict) and group.get("groupId")
        else person
    )
    state = dict((conversation.state_json or {}).get(KIND) or {})
    state["to"] = to
    conversation.state_json = {**(conversation.state_json or {}), KIND: state}
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    line = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "signal reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The receive loop ---------------------------------------------------------------


async def receive_once(
    db: DbSession,
    client: httpx.AsyncClient,
    channel: Channel,
    *,
    timeout_seconds: int = RECEIVE_TIMEOUT_SECONDS,
) -> list[int]:
    """One receive for one channel. Returns the stored lines that are due a reply."""
    credentials = generic.credentials_of(channel)
    number = _number(credentials)
    body = await _call(
        client,
        credentials,
        "GET",
        f"/v1/receive/{quote(number, safe='')}",
        params={
            "timeout": str(timeout_seconds),
            "ignore_attachments": "true",
            "ignore_stories": "true",
            "send_read_receipts": "false",
        },
    )
    identity = {"number": number}
    due: list[int] = []
    for item in body if isinstance(body, list) else []:
        if not isinstance(item, dict):
            continue
        line_id = await ingest(db, channel, {**item, "own": identity})
        if line_id is not None:
            due.append(line_id)
    return due


async def _report(
    sessionmaker: async_sessionmaker, channel_id: int, *, ok: bool, detail: str = ""
) -> None:
    from api.channels import health

    async with session_scope(sessionmaker) as db:
        channel = await db.scalar(select(Channel).where(Channel.id == channel_id))
        if channel is None:
            return
        if ok:
            await health.report_ok(db, channel)
        else:
            await health.report_down(db, channel, detail=detail or "receive failed")


async def _connection(sessionmaker: async_sessionmaker, channel_id: int) -> None:
    """One channel's receive, forever: backs off from 5 s to 300 s while it fails."""
    backoff = 5.0
    async with make_client() as client:
        while True:
            try:
                async with session_scope(sessionmaker) as db:
                    channel = await db.scalar(
                        select(Channel).where(
                            Channel.id == channel_id, Channel.status == "active"
                        )
                    )
                    if channel is None:
                        return
                    due = await receive_once(db, client, channel)
                for line_id in due:
                    schedule_reply(sessionmaker, channel_id, line_id)
                await _report(sessionmaker, channel_id, ok=True)
                backoff = 5.0
                continue
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "signal receive failed",
                    extra={"channel_id": channel_id, "error": str(error)[:200]},
                )
                await _report(sessionmaker, channel_id, ok=False, detail=str(error)[:200])
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)


async def reconcile(
    sessionmaker: async_sessionmaker,
    running: dict[int, tuple[dict[str, str], asyncio.Task]],
) -> None:
    """One supervisor pass: a connection for every active channel, and no other.

    A channel whose fields changed is restarted, so a new address or number takes
    effect without a process restart.
    """
    from api.channels import health

    async with session_scope(sessionmaker) as db:
        rows = await health.usable_channels(db, (KIND,))
        wanted = {row.id: generic.credentials_of(row) for row in rows}

    for channel_id, (credentials, task) in list(running.items()):
        if channel_id not in wanted or wanted[channel_id] != credentials or task.done():
            task.cancel()
            running.pop(channel_id)
    for channel_id, credentials in wanted.items():
        if channel_id not in running:
            running[channel_id] = (
                credentials,
                asyncio.create_task(_connection(sessionmaker, channel_id)),
            )


async def loop(sessionmaker: async_sessionmaker) -> None:
    """The supervisor, started and cancelled by the app lifespan."""
    running: dict[int, tuple[dict[str, str], asyncio.Task]] = {}
    try:
        while True:
            try:
                await reconcile(sessionmaker, running)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("signal supervisor pass failed")
            await asyncio.sleep(SUPERVISE_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
