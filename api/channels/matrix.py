"""The Matrix transport — a bot account on the customer's own homeserver, §B13.

The customer makes an account for the bot on a homeserver they use and pastes its
access token; Tel-Agent holds no shared application. Inbound is the Client-Server
API's **long-poll sync** (`/_matrix/client/v3/sync`), outbound is a room message send.
Both are calls this installation makes, so it needs no public address: the LAN
deployment keeps working with a homeserver on the same network or across the internet.

**The sync position is kept, and the history is not answered.** `next_batch` is
persisted per channel in `settings_json`, so a restart resumes where it stopped. The
very first sync of a channel only takes that position: a bot that answered every line
of every room it had ever joined, the moment somebody switched it on, would be a
flood nobody asked for.

**Invites are accepted.** Somebody who wants to talk to the business invites the bot to
a room, and a bot that waits for an operator to click "join" never answers anybody. An
invite that says `is_direct` marks the room as a direct chat.

**Where the bot answers is a policy.** In a direct chat it always answers. In a room
with other people it answers only when addressed - named in the message's
`m.mentions`, or by its user id or display name in the text - and the address is
stripped before the text enters the record. A room is direct when its invite said so
or when the homeserver reports two members or fewer.

**Unencrypted rooms only, and the card says so.** End-to-end encryption needs device
keys and an Olm/Megolm implementation, which is a dependency this project does not
take. An encrypted event is skipped rather than half-understood.

**Bots are never customers.** The bot's own events are skipped, and so is `m.notice`,
which the specification reserves for automated senders precisely so that two bots do
not answer each other forever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import uuid
from types import ModuleType
from typing import Any
from urllib.parse import quote

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

logger = logging.getLogger("api.matrix")

KIND = "matrix"
INBOUND = "dial_out"

# The specification sets no message length; homeservers cap an event at 65536 bytes of
# JSON. Well under that once escaping and the envelope are counted.
MESSAGE_MAX = 16000

# How long one sync waits on the homeserver for something to happen.
SYNC_TIMEOUT_MS = 30_000

# How often the supervisor reconciles running connections against the channels table.
SUPERVISE_SECONDS = 15.0

# Only what the answering policy reads. Presence and account data would be most of the
# traffic of a busy account and none of the answers.
SYNC_FILTER = json.dumps(
    {
        "presence": {"types": []},
        "account_data": {"types": []},
        "room": {
            "timeline": {"limit": 50, "types": ["m.room.message", "m.room.encrypted"]},
            "state": {"types": ["m.room.member"], "lazy_load_members": True},
            "ephemeral": {"types": []},
            "account_data": {"types": []},
        },
    },
    separators=(",", ":"),
)

SETUP = Setup(
    kind=KIND,
    title="Matrix",
    note="A bot account on your own homeserver answers direct chats and mentions. "
    "Unencrypted rooms only: messages in end-to-end encrypted rooms are not read.",
    guide_url="https://spec.matrix.org/latest/client-server-api/#login",
    fields=(
        Field(
            "homeserver_url",
            "Homeserver URL",
            secret=False,
            help="The address clients use to reach your homeserver.",
            placeholder="https://matrix.example.com",
        ),
        Field(
            "user_id",
            "Bot user ID",
            secret=False,
            help="The full ID of the bot account.",
            placeholder="@tel-agent:example.com",
        ),
        Field(
            "access_token",
            "Access token",
            secret=True,
            help="An access token for the bot account, from a login or your client's settings.",
        ),
    ),
    verified_live=False,
)


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


def make_client() -> httpx.AsyncClient:
    # Longer than one sync's own wait, or every quiet long poll would end as a timeout.
    return httpx.AsyncClient(timeout=httpx.Timeout(SYNC_TIMEOUT_MS / 1000 + 30))


def _base(credentials: dict[str, str]) -> str:
    return credentials.get("homeserver_url", "").strip().rstrip("/")


def _headers(credentials: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {credentials.get('access_token', '')}"}


async def _call(
    client: httpx.AsyncClient,
    credentials: dict[str, str],
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """One Client-Server API call. `ChannelRefused` when the homeserver says no."""
    base = _base(credentials)
    if not base or not credentials.get("access_token"):
        raise ChannelRefused("homeserver_url and access_token are required")
    response = await client.request(
        method, f"{base}/_matrix/client/v3{path}", headers=_headers(credentials), **kwargs
    )
    if response.status_code >= 400:
        try:
            code = str(response.json().get("errcode") or response.status_code)
        except ValueError:
            code = str(response.status_code)
        raise ChannelRefused(f"homeserver refused {path.split('?')[0]}: {code}")
    try:
        body = response.json()
    except ValueError as error:
        raise ChannelRefused("homeserver did not answer with JSON") from error
    return body if isinstance(body, dict) else {}


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The test button: who the token belongs to, and that it is the configured bot."""
    whoami = await _call(client, credentials, "GET", "/account/whoami")
    user_id = str(whoami.get("user_id") or "")
    if not user_id:
        raise ChannelRefused("homeserver did not say whose token this is")
    configured = credentials.get("user_id", "").strip()
    if configured and configured != user_id:
        # A token for a different account would answer as somebody the card does not
        # name, and the bot's own messages would stop being recognised as its own.
        raise ChannelRefused("this access token belongs to a different account")
    return user_id


def split_text(text: str, limit: int = MESSAGE_MAX) -> list[str]:
    return generic.split_on_words(text, limit)


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One `m.text` message into a room. The transaction id makes a retry idempotent."""
    room = quote(target, safe="")
    await _call(
        client,
        credentials,
        "PUT",
        f"/rooms/{room}/send/m.room.message/{uuid.uuid4().hex}",
        json={"msgtype": "m.text", "body": text},
    )


def _localpart(user_id: str) -> str:
    return user_id[1:].split(":", 1)[0] if user_id.startswith("@") else user_id


def message_text(event: Any, identity: Any) -> str | None:
    """The answering policy: the text to answer, or `None` to ignore the event.

    `event` is one timeline event wrapped with where it arrived: `{"room_id", "direct",
    "event"}`. `identity` is the bot, `{"user_id", "display_name"}`.
    """
    if not isinstance(event, dict) or not isinstance(identity, dict):
        return None
    raw = event.get("event")
    if not isinstance(raw, dict) or raw.get("type") != "m.room.message":
        return None

    own = str(identity.get("user_id") or "")
    sender = str(raw.get("sender") or "")
    if not own or not sender or sender == own:
        return None

    content = raw.get("content")
    if not isinstance(content, dict) or content.get("msgtype") != "m.text":
        # `m.notice` is how an automated sender marks itself; images and files are not
        # text the agent can answer.
        return None
    relates = content.get("m.relates_to")
    if isinstance(relates, dict) and relates.get("rel_type") == "m.replace":
        # An edit of a message already answered, not a new question.
        return None

    body = str(content.get("body") or "").strip()
    if not body:
        return None
    if event.get("direct"):
        return body

    mentions = content.get("m.mentions")
    named = isinstance(mentions, dict) and own in (mentions.get("user_ids") or [])

    # Anywhere in the text, only the full id counts: it cannot occur by accident. A
    # display name or a localpart counts only where a person puts an address - at the
    # very start, the way a client's mention pill renders ("Support: when do you
    # open?") - because "support" in the middle of a sentence is just a word.
    full_id = re.compile(rf"(?<![\w@]){re.escape(own)}(?![\w-]|[.:]\w)", re.IGNORECASE)
    names = [_localpart(own)]
    display = str(identity.get("display_name") or "").strip()
    if display:
        names.insert(0, display)
    leading = re.compile(
        r"^\s*@?(?:" + "|".join(re.escape(name) for name in names) + r")\s*[:,](?=\s|$)",
        re.IGNORECASE,
    )
    addressed = named or full_id.search(body) is not None or leading.match(body) is not None
    if not addressed:
        return None
    cleaned = " ".join(leading.sub(" ", full_id.sub(" ", body), count=1).split())
    return cleaned.lstrip(":, ") or None


def reply_target(conversation: Conversation) -> str | None:
    """The room this person last wrote in - where an answer to them belongs."""
    state = (conversation.state_json or {}).get(KIND) or {}
    return str(state.get("room_id") or "") or None


async def ingest(db: DbSession, channel: Channel, event: Any) -> int | None:
    """Store one inbound message. The stored line's id when a reply is due, else `None`.

    One conversation per person, as on every other chat channel: the record follows the
    customer, and the answer goes to the room they last wrote in.
    """
    text = message_text(event, event.get("own") if isinstance(event, dict) else None)
    if text is None:
        return None
    raw = event["event"]
    sender = str(raw["sender"])
    event_id = str(raw.get("event_id") or "")

    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[sender])
    if decision.action == "block":
        logger.info(
            "matrix message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await generic.conversation_for(db, channel, sender)
    if generic.seen_before(conversation, KIND, event_id):
        logger.info(
            "matrix event repeated, dropped", extra={"conversation_id": conversation.id}
        )
        return None
    state = dict((conversation.state_json or {}).get(KIND) or {})
    state["room_id"] = str(event.get("room_id") or "")
    conversation.state_json = {**(conversation.state_json or {}), KIND: state}
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    line = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "matrix reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The sync ---------------------------------------------------------------------


async def identity_of(client: httpx.AsyncClient, credentials: dict[str, str]) -> dict[str, str]:
    """The bot as the answering policy needs it. The display name is best effort."""
    user_id = await probe(client, credentials)
    display = ""
    try:
        profile = await _call(
            client, credentials, "GET", f"/profile/{quote(user_id, safe='')}/displayname"
        )
        display = str(profile.get("displayname") or "")
    except (ChannelRefused, httpx.HTTPError):
        pass
    return {"user_id": user_id, "display_name": display}


def _is_invite_direct(invite: dict[str, Any], own: str) -> bool:
    for state in (invite.get("invite_state") or {}).get("events") or []:
        if (
            isinstance(state, dict)
            and state.get("type") == "m.room.member"
            and state.get("state_key") == own
            and (state.get("content") or {}).get("is_direct") is True
        ):
            return True
    return False


async def sync_once(
    db: DbSession,
    client: httpx.AsyncClient,
    channel: Channel,
    identity: dict[str, str],
    *,
    timeout_ms: int = SYNC_TIMEOUT_MS,
) -> list[int]:
    """One sync for one channel. Returns the stored lines that are due a reply.

    The new position is committed before anything is answered, as the Telegram offset
    is: a crash mid-reply loses one answer rather than replaying the batch forever.
    """
    credentials = generic.credentials_of(channel)
    stored = dict((channel.settings_json or {}).get(KIND) or {})
    since = str(stored.get("next_batch") or "")
    params: dict[str, Any] = {"filter": SYNC_FILTER, "timeout": timeout_ms if since else 0}
    if since:
        params["since"] = since

    body = await _call(client, credentials, "GET", "/sync", params=params)
    next_batch = str(body.get("next_batch") or "")
    rooms = body.get("rooms") or {}
    own = identity["user_id"]
    direct = {str(room) for room in stored.get("direct_rooms") or []}

    for room_id, invite in (rooms.get("invite") or {}).items():
        try:
            await _call(
                client, credentials, "POST", f"/join/{quote(room_id, safe='')}", json={}
            )
        except (ChannelRefused, httpx.HTTPError) as error:
            logger.info(
                "matrix invite not accepted",
                extra={"channel_id": channel.id, "error": str(error)[:200]},
            )
            continue
        if isinstance(invite, dict) and _is_invite_direct(invite, own):
            direct.add(room_id)

    joined = rooms.get("join") or {}
    events: list[dict[str, Any]] = []
    for room_id, room in joined.items():
        if not isinstance(room, dict):
            continue
        members = (room.get("summary") or {}).get("m.joined_member_count")
        if isinstance(members, int):
            if members <= 2:
                direct.add(room_id)
            else:
                direct.discard(room_id)
        if not since:
            continue  # The first sync takes the position; it answers no history.
        for raw in (room.get("timeline") or {}).get("events") or []:
            if isinstance(raw, dict):
                events.append({"room_id": room_id, "event": raw, "own": identity})

    stored["next_batch"] = next_batch or since
    stored["direct_rooms"] = sorted(direct)
    channel.settings_json = {**(channel.settings_json or {}), KIND: stored}
    await db.commit()

    due: list[int] = []
    for event in events:
        event["direct"] = event["room_id"] in direct
        line_id = await ingest(db, channel, event)
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
            await health.report_down(db, channel, detail=detail or "sync failed")


async def _connection(sessionmaker: async_sessionmaker, channel_id: int) -> None:
    """One channel's sync, forever: backs off from 5 s to 300 s while it fails."""
    backoff = 5.0
    identity: dict[str, str] | None = None
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
                    if identity is None:
                        identity = await identity_of(client, generic.credentials_of(channel))
                    due = await sync_once(db, client, channel, identity)
                for line_id in due:
                    schedule_reply(sessionmaker, channel_id, line_id)
                await _report(sessionmaker, channel_id, ok=True)
                backoff = 5.0
                continue
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "matrix sync failed",
                    extra={"channel_id": channel_id, "error": str(error)[:200]},
                )
                identity = None
                await _report(sessionmaker, channel_id, ok=False, detail=str(error)[:200])
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)


async def reconcile(
    sessionmaker: async_sessionmaker,
    running: dict[int, tuple[dict[str, str], asyncio.Task]],
) -> None:
    """One supervisor pass: a connection for every active channel, and no other.

    A channel whose credentials changed is restarted, so a rotated token takes effect
    without a process restart.
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
                logger.exception("matrix supervisor pass failed")
            await asyncio.sleep(SUPERVISE_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
