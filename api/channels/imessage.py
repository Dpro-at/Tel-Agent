"""The iMessage transport — the REST bridge on the customer's own Mac, §B13.

Apple publishes no server API for iMessage. The customer runs a bridge server on a Mac
signed in to Messages, on the same network as Tel-Agent, and gives the card two things:
the bridge's address and its password. Tel-Agent holds no shared application and
exposes nothing.

**Dial-out, by polling with a cursor.** The bridge keeps every message in the Mac's own
database, so reading does not take them off it the way a queue would. The newest
message date seen, in milliseconds, is kept per channel in `settings_json` with the ids
of the messages at that date, so a restart resumes where it stopped and the edge is not
read twice. The first poll only takes the position: switching a channel on answers nothing
that was written before it. A message that is already a day old when it arrives is
dropped as well, so a Tel-Agent that was down for a week does not reply to last week.

**The password travels in the query string, because that is the only place the bridge
reads it.** It is therefore never in anything this module logs or reports: a refusal
names the endpoint and the bridge's own words, and a transport failure is reported by
its type, never by its text, since an HTTP library's error may quote the URL it failed
on. A plain-HTTP address is accepted only where it cannot cross the internet: a loopback
or private address, `localhost`, a hostname with no dot, or a local-only suffix.

**Sending prefers the private API and falls back.** The private-API method is what a
bridge with its helper installed sends through; a bridge without one refuses it, and the
same message is sent again with the standard method. Both attempts carry one temporary
id, which is what the bridge deduplicates on, so a first attempt that went through
after all is not delivered twice. A channel whose bridge refused once sends the
standard way from then on, until its fields are written again.

**Where the account answers is a policy.** A one-to-one chat is always answered.
iMessage has no mentions, so a group chat is answered only when a message starts with
the name set on the card, and the name is cut before the text reaches the model. With no
name set, groups are never answered. The account's own messages, including those sent
from the owner's other devices, are never answered; iMessage has no bot accounts, so the
account itself is the one automated sender that can be recognised. Reactions and group
events carry no text to answer.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import sys
import time
import uuid
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

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

logger = logging.getLogger("api.imessage")

KIND = "imessage"
INBOUND = "dial_out"

# iMessage documents no length limit. Kept to what reads as one message on a phone.
MESSAGE_MAX = 2000

# How often one channel asks the bridge for new messages, in seconds. The bridge
# answers from a database on the same network, so asking often costs little.
POLL_SECONDS = 3.0

# The most messages one poll takes. More than that waits for the next poll, which
# starts after the newest one taken.
QUERY_LIMIT = 100

# How often the supervisor reconciles running connections against the channels table.
SUPERVISE_SECONDS = 15.0

# Older than this on arrival, and a message is a backlog rather than a conversation.
STALE_SECONDS = 24 * 60 * 60

# The two ways the bridge can send: through its helper, and the standard one.
PRIVATE_API = "private-api"
STANDARD = "apple-script"

# The bridge's chat style for a group, and the separator a group chat's id carries.
GROUP_STYLE = 43
GROUP_MARK = ";+;"

# Hostnames that only resolve on a local network.
_LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa")

SETUP = Setup(
    kind=KIND,
    title="iMessage",
    note="The Apple ID signed in to Messages on your own Mac answers direct messages, "
    "through the REST bridge server you run on that Mac. Tel-Agent collects messages "
    "from it and exposes nothing to the internet.",
    guide_url="https://support.apple.com/guide/messages/welcome/mac",
    fields=(
        Field(
            "base_url",
            "Bridge address",
            secret=False,
            help="Where Tel-Agent reaches the bridge. Plain http only on your own network.",
            placeholder="http://192.168.1.20:1234",
        ),
        Field(
            "password",
            "Bridge password",
            secret=True,
            help="The server password set in the bridge's settings.",
        ),
        Field(
            "group_name",
            "Name to answer to in groups",
            secret=False,
            required=False,
            help="A group message is answered only when it starts with this name. "
            "Leave empty and group chats are never answered.",
            placeholder="Tel-Agent",
        ),
    ),
    verified_live=True,
)


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0))


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
    """The bridge's address, refused when it would carry the password over plain HTTP."""
    raw = credentials.get("base_url", "").strip().rstrip("/")
    if not raw:
        raise ChannelRefused("base_url is required")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ChannelRefused("base_url must be an http or https address")
    if parts.scheme == "http" and not _is_local_host(parts.hostname):
        raise ChannelRefused("a bridge on a public address must use https")
    return raw


def _password(credentials: dict[str, str]) -> str:
    password = credentials.get("password", "")
    if not password:
        raise ChannelRefused("password is required")
    return password


async def _call(
    client: httpx.AsyncClient,
    credentials: dict[str, str],
    method: str,
    path: str,
    **kwargs: Any,
) -> Any:
    """One bridge call, the `data` of its answer. `ChannelRefused` when the bridge says no.

    Nothing raised from here carries the URL: it holds the password.
    """
    response = await client.request(
        method,
        f"{base_url(credentials)}{path}",
        params={"password": _password(credentials)},
        **kwargs,
    )
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code >= 400:
        detail = ""
        if isinstance(body, dict):
            error = body.get("error")
            detail = str(
                (error.get("message") if isinstance(error, dict) else None)
                or body.get("message")
                or ""
            )
        raise ChannelRefused(
            f"the bridge refused {path}: {(detail or str(response.status_code))[:120]}"
        )
    if not isinstance(body, dict):
        raise ChannelRefused("the bridge did not answer with JSON")
    return body.get("data")


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The test button: the bridge answers the password, and Messages is signed in."""
    about = await _call(client, credentials, "GET", "/api/v1/server/info")
    account = ""
    if isinstance(about, dict):
        account = str(about.get("detected_imessage") or about.get("detected_icloud") or "")
    if not account:
        raise ChannelRefused("the bridge reports no Apple ID signed in to Messages")
    return account


async def activate(
    client: httpx.AsyncClient, credentials: dict[str, str], webhook_url: str | None
) -> None:
    """Switching on checks the address and the password, without calling the bridge.

    A Mac that is asleep for a moment should not stop anybody switching the channel on;
    the supervisor reports it and keeps trying. An address that would send the password
    over plain HTTP across the internet is never going to be right, so that is refused.
    """
    base_url(credentials)
    _password(credentials)


def split_text(text: str, limit: int = MESSAGE_MAX) -> list[str]:
    return generic.split_on_words(text, limit)


# The channels whose bridge refused the private API and then sent the standard way.
# Their next answers go the standard way first, rather than paying a refusal each time;
# `credentials_changed` forgets a channel, so a bridge given its helper is asked again.
_STANDARD_ONLY: set[str] = set()


def credentials_changed(channel_id: int) -> None:
    _STANDARD_ONLY.discard(str(channel_id))


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One text message to a chat, through the private API when the bridge has it."""
    body = {"chatGuid": target, "message": text, "tempGuid": f"tel-agent-{uuid.uuid4()}"}
    channel = credentials.get(generic.CHANNEL_ID, "")
    if channel not in _STANDARD_ONLY:
        try:
            await _call(
                client,
                credentials,
                "POST",
                "/api/v1/message/text",
                json={**body, "method": PRIVATE_API},
            )
            return
        except ChannelRefused:
            pass
    await _call(
        client,
        credentials,
        "POST",
        "/api/v1/message/text",
        json={**body, "method": STANDARD},
    )
    if channel:
        _STANDARD_ONLY.add(channel)


def _chat(event: dict[str, Any]) -> dict[str, Any]:
    """The chat a message was written in. A message belongs to exactly one."""
    chats = event.get("chats")
    first = chats[0] if isinstance(chats, list) and chats else None
    return first if isinstance(first, dict) else {}


def _is_group(chat: dict[str, Any]) -> bool:
    return chat.get("style") == GROUP_STYLE or GROUP_MARK in str(chat.get("guid") or "")


def _sender(event: dict[str, Any]) -> str:
    handle = event.get("handle")
    return str(handle.get("address") or "") if isinstance(handle, dict) else ""


def _addressed(text: str, name: str) -> str | None:
    """The text after the name it starts with, or `None` when it does not start with it.

    The name matches in any case, after an optional `@`, and only as a whole word:
    "Anna" addresses nobody in "Annabel, are you there?".
    """
    match = re.match(rf"@?{re.escape(name)}(?![\w])[\s,.:;!?-]*", text, re.IGNORECASE)
    if match is None:
        return None
    return text[match.end() :].strip() or None


def message_text(event: Any, identity: Any) -> str | None:
    """The answering policy. `event` is one message; `identity` holds the group name."""
    if not isinstance(event, dict) or not isinstance(identity, dict):
        return None
    if event.get("isFromMe"):
        return None
    if event.get("associatedMessageGuid") or event.get("associatedMessageType"):
        # A reaction to another message: its text only describes the reaction.
        return None
    if int(event.get("itemType") or 0) != 0:
        # A group renamed, a person added or removed.
        return None
    text = str(event.get("text") or "").strip()
    if not text or not _sender(event):
        return None

    chat = _chat(event)
    if not chat.get("guid"):
        return None
    if not _is_group(chat):
        return text
    name = str(identity.get("group_name") or "").strip()
    return _addressed(text, name) if name else None


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
    person = _sender(event)

    sent_ms = int(event.get("dateCreated") or 0)
    if sent_ms and time.time() - sent_ms / 1000 > STALE_SECONDS:
        logger.info(
            "imessage message too old to answer, dropped", extra={"channel_id": channel.id}
        )
        return None

    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[person])
    if decision.action == "block":
        logger.info(
            "imessage message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await generic.conversation_for(db, channel, person)
    if generic.seen_before(conversation, KIND, str(event.get("guid") or "")):
        logger.info(
            "imessage message repeated, dropped", extra={"conversation_id": conversation.id}
        )
        return None

    state = dict((conversation.state_json or {}).get(KIND) or {})
    state["to"] = str(_chat(event)["guid"])
    conversation.state_json = {**(conversation.state_json or {}), KIND: state}
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    line = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "imessage reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The poll loop --------------------------------------------------------------


async def receive_once(db: DbSession, client: httpx.AsyncClient, channel: Channel) -> list[int]:
    """One poll for one channel. Returns the stored lines that are due a reply.

    The new position is committed before anything is stored, as the other cursors are:
    a crash mid-batch loses those messages rather than replaying them forever.
    """
    credentials = generic.credentials_of(channel)
    stored = dict((channel.settings_json or {}).get(KIND) or {})
    after = int(stored.get("after") or 0)
    if not after:
        # The first poll takes the position; it answers no history.
        stored["after"] = int(time.time() * 1000)
        channel.settings_json = {**(channel.settings_json or {}), KIND: stored}
        await db.commit()
        return []

    body = await _call(
        client,
        credentials,
        "POST",
        "/api/v1/message/query",
        json={
            "limit": QUERY_LIMIT,
            "offset": 0,
            "after": after,
            "sort": "ASC",
            "with": ["chat", "handle"],
        },
    )
    # `after` includes its own millisecond, so the messages at the edge come back on
    # every poll. Their ids are kept beside the position and skipped here: the
    # conversation's own dedup would miss them once that conversation is closed.
    edge = {str(guid) for guid in stored.get("edge") or []}
    items = [
        item
        for item in (body if isinstance(body, list) else [])
        if isinstance(item, dict)
        and not (int(item.get("dateCreated") or 0) == after and str(item.get("guid")) in edge)
    ]

    newest = max([after, *(int(item.get("dateCreated") or 0) for item in items)])
    at_newest = {str(item.get("guid")) for item in items if item.get("dateCreated") == newest}
    stored["edge"] = sorted(at_newest | edge) if newest == after else sorted(at_newest)
    stored["after"] = newest
    channel.settings_json = {**(channel.settings_json or {}), KIND: stored}
    await db.commit()

    identity = {"group_name": credentials.get("group_name", "")}
    due: list[int] = []
    for item in items:
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
            await health.report_down(db, channel, detail=detail or "poll failed")


def _describe(error: Exception) -> str:
    """What a failure may say about itself. Only a refusal's text is known to be clean."""
    return str(error)[:200] if isinstance(error, ChannelRefused) else type(error).__name__


async def _connection(sessionmaker: async_sessionmaker, channel_id: int) -> None:
    """One channel's poll, forever: backs off from 5 s to 300 s while it fails."""
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
                await asyncio.sleep(POLL_SECONDS)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as error:
                detail = _describe(error)
                logger.warning(
                    "imessage poll failed", extra={"channel_id": channel_id, "error": detail}
                )
                await _report(sessionmaker, channel_id, ok=False, detail=detail)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)


async def reconcile(
    sessionmaker: async_sessionmaker,
    running: dict[int, tuple[dict[str, str], asyncio.Task]],
) -> None:
    """One supervisor pass: a connection for every active channel, and no other.

    A channel whose fields changed is restarted, so a new address or password takes
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
                logger.exception("imessage supervisor pass failed")
            await asyncio.sleep(SUPERVISE_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
