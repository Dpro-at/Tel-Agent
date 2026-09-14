"""The Viber transport — the customer's own bot account, through the public door, §B13.

The customer creates a bot account in their own Viber admin panel and pastes its
authentication token, and names the sender its messages go out under. Tel-Agent holds
no shared application. Viber delivers events to the channel's webhook - registered with
Viber when the channel is switched on - and answers go out through the REST bot API.

**The signature is the door's whole defence.** Every callback carries
`X-Viber-Content-Signature`: hex HMAC-SHA256 of the raw body under the auth token. It is
checked over the bytes before anything is parsed, in constant time, and a callback that
fails it gets the one refusal every other failure gets.

**Switching on registers the webhook, and Viber checks it on the spot.** `set_webhook`
makes Viber call the address with a `webhook` event before it answers, so the generic
route commits the switch before calling `activate` and the door is already open for the
check. Viber only calls an address with a certificate from a trusted authority, so an
installation without a public `https` address is refused before Viber is asked.

**A Viber bot talks one to one.** Bot accounts are not members of group chats, so every
message the door receives is a person writing to the business, and every one is
answered. Delivery and read receipts, subscriptions and the "conversation started"
event are acknowledged and not answered, and neither is anything that is not text.

**Errors arrive as a successful response.** The REST API answers HTTP 200 with a
non-zero `status` when it refuses, so the status is what is read, not the HTTP code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sys
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
from api.models import Channel

logger = logging.getLogger("api.viber")

KIND = "viber"
INBOUND = "door"

API_BASE = "https://chatapi.viber.com/pa"

# One text message. The platform's own limit.
MESSAGE_MAX = 7000

# The platform's own limit on the sender name a message goes out under.
SENDER_NAME_MAX = 28

SIGNATURE_HEADER = "X-Viber-Content-Signature"
TOKEN_HEADER = "X-Viber-Auth-Token"  # noqa: S105 - a header name, not a token

# Only messages are needed to answer anybody. Viber adds the events it will not let an
# account switch off.
EVENT_TYPES = ["message"]

SETUP = Setup(
    kind=KIND,
    title="Viber",
    note="A bot account from your own Viber admin panel answers people who message it. "
    "Viber only calls an https address with a trusted certificate, so this installation "
    "needs a public address before the channel can be switched on.",
    guide_url="https://developers.viber.com/docs/api/rest-bot-api/",
    fields=(
        Field(
            "auth_token",
            "Authentication token",
            secret=True,
            help="Shown in the bot account's info in your Viber admin panel.",
        ),
        Field(
            "sender_name",
            "Sender name",
            secret=False,
            help="The name your answers are sent under, up to 28 characters.",
        ),
    ),
    verified_live=False,
)


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(15.0))


async def _call(
    client: httpx.AsyncClient, credentials: dict[str, str], path: str, body: dict[str, Any]
) -> dict[str, Any]:
    """One REST bot API call. `ChannelRefused` when Viber says no, however it says it."""
    token = credentials.get("auth_token", "")
    if not token:
        raise ChannelRefused("this channel has no authentication token")
    response = await client.post(path, headers={TOKEN_HEADER: token}, json=body)
    try:
        answer = response.json()
    except ValueError as error:
        raise ChannelRefused(f"Viber did not answer {path} with JSON") from error
    if not isinstance(answer, dict):
        raise ChannelRefused(f"Viber did not answer {path} with an object")
    status = answer.get("status")
    if response.status_code >= 400 or status != 0:
        detail = str(answer.get("status_message") or status or response.status_code)
        raise ChannelRefused(f"Viber refused {path}: {detail[:120]}")
    return answer


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The test button: the bot account the token belongs to."""
    info = await _call(client, credentials, "/get_account_info", {})
    name = str(info.get("name") or "")
    uri = str(info.get("uri") or "")
    if not name and not uri:
        raise ChannelRefused("Viber did not say which account this is")
    return f"{name} ({uri})" if name and uri else name or uri


async def activate(
    client: httpx.AsyncClient, credentials: dict[str, str], webhook_url: str | None
) -> None:
    """Register the door with Viber, which calls it back before it answers."""
    if not webhook_url or not webhook_url.startswith("https://"):
        raise ChannelRefused(
            "Viber only calls an https address; set PUBLIC_BASE_URL to this "
            "installation's public https address"
        )
    await _call(
        client,
        credentials,
        "/set_webhook",
        {
            "url": webhook_url,
            "event_types": EVENT_TYPES,
            "send_name": True,
            "send_photo": False,
        },
    )


def split_text(text: str, limit: int = MESSAGE_MAX) -> list[str]:
    return generic.split_on_words(text, limit)


def signature_for(auth_token: str, body: bytes) -> str:
    return hmac.new(auth_token.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(auth_token: str, body: bytes, header: str | None) -> bool:
    """Whether this header is the signature over these exact bytes.

    The ASCII test keeps `hmac.compare_digest` from raising on a header of non-ASCII
    text, which would answer a stranger with a 500 instead of the one refusal.
    """
    if not auth_token or not header or not header.isascii():
        return False
    return hmac.compare_digest(header.lower(), signature_for(auth_token, body))


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One text message to one subscriber, under the card's sender name."""
    sender = credentials.get("sender_name", "").strip()[:SENDER_NAME_MAX]
    if not sender:
        raise ChannelRefused("this channel has no sender name")
    await _call(
        client,
        credentials,
        "/send_message",
        {"receiver": target, "type": "text", "sender": {"name": sender}, "text": text},
    )


def message_text(event: Any, identity: Any) -> str | None:
    """The answering policy. `event` is one callback; `identity` is unused.

    A bot account has no group chats, so there is no addressing to check: a text
    message from a person is answered, and every other event is not.
    """
    if not isinstance(event, dict) or event.get("event") != "message":
        return None
    message = event.get("message")
    if not isinstance(message, dict) or message.get("type") != "text":
        return None
    sender = event.get("sender")
    if not isinstance(sender, dict) or not sender.get("id"):
        return None
    text = str(message.get("text") or "").strip()
    return text or None


async def ingest(db: DbSession, channel: Channel, event: Any) -> int | None:
    """Store one message callback. The stored line's id when a reply is due, else `None`."""
    text = message_text(event, None)
    if text is None:
        return None
    person = str(event["sender"]["id"])

    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[person])
    if decision.action == "block":
        logger.info(
            "viber message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    title = str(event["sender"].get("name") or "") or None
    conversation, started = await generic.conversation_for(db, channel, person, title)
    if generic.seen_before(conversation, KIND, str(event.get("message_token") or "")):
        logger.info("viber event repeated, dropped", extra={"conversation_id": conversation.id})
        return None
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    line = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, line, started)
    if conversation.handling == "human":
        logger.info(
            "viber reply withheld, a person has the thread",
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
    """One callback: verify the bytes, store a message, acknowledge, answer afterwards.

    Every failure raises, and the public route turns any raise into the one refusal.
    """
    token = str(generic.secrets_of(channel).get("auth_token") or "")
    body = await request.body()
    if not verify_signature(token, body, request.headers.get(SIGNATURE_HEADER)):
        logger.info(
            "viber delivery refused",
            extra={"reason": "bad signature", "channel_id": channel.id},
        )
        raise ChannelRefused("the signature did not check out")

    event = json.loads(body)
    if not isinstance(event, dict):
        raise ValueError("the callback is not a JSON object")

    channel_id = channel.id
    line_id = await ingest(db, channel, event)
    if line_id is not None:
        schedule_reply(request.app.state.sessionmaker, channel_id, line_id)
    # The `webhook` check and every receipt are acknowledged the same way.
    return Response(content="{}", media_type="application/json")
