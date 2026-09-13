"""The LINE transport — the customer's own official account, through the public door, §B13.

The customer creates a Messaging API channel for their official account in their own
LINE Developers console and pastes its channel secret and channel access token.
Tel-Agent holds no shared provider. LINE delivers events to the channel's webhook - the
address the settings card prints - and answers go out through the Messaging API.

**The signature is the door's whole defence.** Every delivery carries
`x-line-signature`: base64 HMAC-SHA256 of the raw body under the channel secret. It is
checked over the bytes before anything is parsed, in constant time, and a delivery that
fails it gets the one refusal every other failure gets. The console's "Verify" button
sends a signed delivery with no events; it is acknowledged like any other.

**Reply first, push when the reply is spent.** An event carries a reply token that
answers it free of charge, once, for about a minute. A push message counts against the
account's monthly allowance. The answer therefore goes out as a reply while the token
is fresh, and as a push only when it has expired or was already used - which is also
how a person's reply after a takeover reaches the customer.

**Where the account answers is a policy.** In a one-to-one chat it always answers. In a
group or a multi-person chat it answers only when it is mentioned, and the mention is
cut out of the text before it is stored. The account's own events are never answered.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import sys
import time
from types import ModuleType
from typing import Any

import httpx
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import routing
from api.channels import generic
from api.channels.generic import ChannelRefused
from api.channels.setup import Field, Setup
from api.models import Channel, Conversation

logger = logging.getLogger("api.line")

KIND = "line"
INBOUND = "door"

API_BASE = "https://api.line.me"

# One text message. The platform's own limit.
MESSAGE_MAX = 5000

SIGNATURE_HEADER = "x-line-signature"

# The reply token lives for about a minute. Kept well inside that, so a reply that took
# a while to write is pushed rather than refused on arrival.
REPLY_TOKEN_SECONDS = 50

SETUP = Setup(
    kind=KIND,
    title="LINE",
    note="An official account from your own LINE Developers console answers people who "
    "message it, and group chats that mention it. Paste the webhook address below into "
    "the console and switch webhooks on there.",
    guide_url="https://developers.line.biz/en/docs/messaging-api/getting-started/",
    fields=(
        Field(
            "channel_secret",
            "Channel secret",
            secret=True,
            help="From the Basic settings tab of your Messaging API channel.",
        ),
        Field(
            "channel_access_token",
            "Channel access token",
            secret=True,
            help="Issue a long-lived token on the Messaging API tab.",
        ),
    ),
    verified_live=False,
)


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(15.0))


def _headers(credentials: dict[str, str]) -> dict[str, str]:
    token = credentials.get("channel_access_token", "")
    if not token:
        raise ChannelRefused("this channel has no access token")
    return {"Authorization": f"Bearer {token}"}


def _refusal(response: httpx.Response, what: str) -> ChannelRefused:
    try:
        detail = str(response.json().get("message") or response.status_code)
    except ValueError:
        detail = str(response.status_code)
    return ChannelRefused(f"LINE refused {what}: {detail[:120]}")


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The test button: the official account the access token belongs to."""
    response = await client.get("/v2/bot/info", headers=_headers(credentials))
    if response.status_code >= 400:
        raise _refusal(response, "the access token")
    info = response.json()
    name = str(info.get("displayName") or "")
    basic_id = str(info.get("basicId") or "")
    if not name and not basic_id:
        raise ChannelRefused("LINE did not say which account this is")
    return f"{name} ({basic_id})" if name and basic_id else name or basic_id


def split_text(text: str, limit: int = MESSAGE_MAX) -> list[str]:
    return generic.split_on_words(text, limit)


def signature_for(channel_secret: str, body: bytes) -> str:
    digest = hmac.new(channel_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_signature(channel_secret: str, body: bytes, header: str | None) -> bool:
    """Whether this header is the signature over these exact bytes.

    The ASCII test keeps `hmac.compare_digest` from raising on a header of non-ASCII
    text, which would answer a stranger with a 500 instead of the one refusal.
    """
    if not channel_secret or not header or not header.isascii():
        return False
    return hmac.compare_digest(header, signature_for(channel_secret, body))


def _address(target: str) -> tuple[str, str]:
    """A reply target is `<to>` or `<to> <reply token>`."""
    to, _, token = target.partition(" ")
    return to, token


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One text message: as a reply while the token is good, as a push otherwise."""
    to, token = _address(target)
    message = {"type": "text", "text": text}
    headers = _headers(credentials)
    if token:
        replied = await client.post(
            "/v2/bot/message/reply",
            headers=headers,
            json={"replyToken": token, "messages": [message]},
        )
        if replied.status_code < 400:
            return
        # Expired, or already spent on an earlier piece of this answer. A push still
        # reaches the person; only the allowance pays for it.
        logger.info("line reply token not accepted, pushing instead")
    if not to:
        raise ChannelRefused("nobody to push this message to")
    pushed = await client.post(
        "/v2/bot/message/push", headers=headers, json={"to": to, "messages": [message]}
    )
    if pushed.status_code >= 400:
        raise _refusal(pushed, "the message")


def _utf16_cut(text: str, index: int, length: int) -> str | None:
    """`text` without the span the platform describes in UTF-16 code units."""
    units = text.encode("utf-16-le")
    start, end = index * 2, (index + length) * 2
    if index < 0 or length <= 0 or end > len(units):
        return None
    try:
        removed = units[start:end].decode("utf-16-le")
        kept = (units[:start] + units[end:]).decode("utf-16-le")
    except UnicodeDecodeError:
        # A span that splits a surrogate pair is not one this text can have; leave the
        # text whole rather than refuse the delivery it arrived in.
        return None
    return kept if removed.startswith("@") else None


def message_text(event: Any, identity: Any) -> str | None:
    """The answering policy. `event` is one webhook event; `identity` the bot's user id."""
    if not isinstance(event, dict) or event.get("type") != "message":
        return None
    message = event.get("message") or {}
    if message.get("type") != "text":
        return None
    source = event.get("source") or {}
    if identity and source.get("userId") == identity:
        return None
    text = str(message.get("text") or "")
    if not text.strip():
        return None
    if source.get("type") == "user":
        return text.strip()

    mentionees = ((message.get("mention") or {}).get("mentionees")) or []
    mine = [
        entry
        for entry in mentionees
        if isinstance(entry, dict)
        and (entry.get("isSelf") is True or (identity and entry.get("userId") == identity))
    ]
    if not mine:
        return None
    # Cut from the end first, so an earlier span's index is still right.
    for entry in sorted(mine, key=lambda item: int(item.get("index", 0)), reverse=True):
        cut = _utf16_cut(text, int(entry.get("index", -1)), int(entry.get("length", 0)))
        if cut is not None:
            text = cut
    return " ".join(text.split()) or None


def reply_target(conversation: Conversation) -> str | None:
    """Where the answer goes: the chat the person last wrote in, with a fresh reply token."""
    state = (conversation.state_json or {}).get(KIND) or {}
    to = str(state.get("to") or "")
    if not to:
        return None
    token = str(state.get("reply_token") or "")
    at = float(state.get("reply_token_at") or 0)
    fresh = token and time.time() - at < REPLY_TOKEN_SECONDS
    return f"{to} {token}" if fresh else to


async def ingest(db: DbSession, channel: Channel, event: Any) -> int | None:
    """Store one message event. The stored line's id when a reply is due, else `None`."""
    identity = event.get("destination") if isinstance(event, dict) else None
    text = message_text(event, identity)
    if text is None:
        return None
    source = event.get("source") or {}
    chat = str(source.get("groupId") or source.get("roomId") or source.get("userId") or "")
    person = str(source.get("userId") or chat)
    if not person:
        return None

    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[person])
    if decision.action == "block":
        logger.info(
            "line message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await generic.conversation_for(db, channel, person)
    message_id = str(
        (event.get("message") or {}).get("id") or event.get("webhookEventId") or ""
    )
    if generic.seen_before(conversation, KIND, message_id):
        logger.info("line event repeated, dropped", extra={"conversation_id": conversation.id})
        return None
    state = dict((conversation.state_json or {}).get(KIND) or {})
    state.update(
        to=chat,
        reply_token=str(event.get("replyToken") or ""),
        reply_token_at=time.time(),
    )
    conversation.state_json = {**(conversation.state_json or {}), KIND: state}
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    line = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, line, started)
    if conversation.handling == "human":
        logger.info(
            "line reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The door -------------------------------------------------------------------


async def receive(db: DbSession, channel: Channel, request: Request) -> Response:
    """One delivery: verify the bytes, store each event, acknowledge, answer afterwards.

    Every failure raises, and the public route turns any raise into the one refusal.
    """
    secret = str(generic.secrets_of(channel).get("channel_secret") or "")
    body = await request.body()
    if not verify_signature(secret, body, request.headers.get(SIGNATURE_HEADER)):
        logger.info(
            "line delivery refused", extra={"reason": "bad signature", "channel_id": channel.id}
        )
        raise ChannelRefused("the signature did not check out")

    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("the delivery is not a JSON object")
    destination = payload.get("destination")
    channel_id = channel.id
    due: list[int] = []
    for event in payload.get("events") or []:
        if isinstance(event, dict):
            line_id = await ingest(db, channel, {**event, "destination": destination})
            if line_id is not None:
                due.append(line_id)
    for line_id in due:
        schedule_reply(request.app.state.sessionmaker, channel_id, line_id)
    return Response(content="{}", media_type="application/json")
