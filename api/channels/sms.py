"""SMS — a text to a number the business already owns, §B13.

The first channel built on the declarative contract (D-044): no routes of its own, a
`Setup` beside the transport, and the generic card and door serving both. The customer
pastes the credentials of **their own** messaging account; Tel-Agent holds no shared
application here any more than it does anywhere else.

**Inbound is a door.** The platform posts a form body to `/public/sms/{webhook_path}`
and waits for an empty TwiML document. That answer has to be immediate: the platform
gives a webhook about fifteen seconds and then retries, so generating the reply inside
the request would earn the customer two identical texts. The door verifies, stores,
acknowledges, and answers from a task afterwards — the same acknowledge-first split
every webhook channel in this product makes.

**The signature is over the address, not only the body.** The platform signs the exact
public URL it called plus every POST parameter, key and value concatenated in key
order, HMAC-SHA1 under the account's auth token. Including the URL is what stops a
delivery signed for one installation from being replayed at another, so behind a proxy
the forwarded scheme and host are what must be reconstructed — the address the platform
saw, not the one the application server was reached on.

**The conversation is the number.** One sender number is one conversation; there is no
richer identity in SMS and none is invented. A text from the channel's own number is
this installation hearing itself and is dropped, which is the echo guard the webhook
channels all need. Dedup is by the platform's own message id: retries are normal.

**Nothing here is rich.** No buttons, no lists, no attachments — 1600 characters of
plain text, split on word boundaries when the answer is longer. That is the whole
interface, which is why SMS needs the full step machine the phone needs (§B13).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import parse_qsl, urlunsplit

import httpx
from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent.config import ConfigurationError
from agent.reply import reply as generate_reply
from agent.tools import TakenMessage
from api import llm, routing, webhooks
from api.channels.generic import ChannelRefused, credentials_of, secrets_of, shown_of
from api.channels.setup import Field, Setup
from api.conversations import position_ms
from api.db import session_scope
from api.models import Channel, Conversation, Message
from api.notifications import raise_notification

logger = logging.getLogger("api.sms")

KIND = "sms"
INBOUND = "door"

SETUP = Setup(
    kind=KIND,
    title="SMS",
    note="Texts to a number in your own messaging account are answered like any "
    "other conversation.",
    guide_url="https://www.twilio.com/docs/messaging/guides/webhook-request",
    fields=(
        Field(
            "account_sid",
            "Account SID",
            secret=False,
            help="The account identifier shown on your messaging account's console.",
            placeholder="AC…",
        ),
        Field(
            "auth_token",
            "Auth token",
            secret=True,
            help="The auth token of the same account. It also verifies every "
            "delivery that arrives at the address below.",
        ),
        Field(
            "from_number",
            "Sender number",
            secret=False,
            help="The number in that account which receives and sends these texts, "
            "in international format.",
            placeholder="+431234567",
        ),
    ),
    verified_live=True,
)

# Where the messaging REST API lives. Tests replace `make_client` rather than this,
# which is the only seam either of them needs.
API_BASE = "https://api.twilio.com"
API_VERSION = "2010-04-01"

PREVIEW_MAX = 80

# One text may carry 1600 characters; a longer answer goes out as several.
MESSAGE_MAX = 1600

# The header the platform proves itself with, and the empty document it waits for.
SIGNATURE_HEADER = "X-Twilio-Signature"
ACKNOWLEDGEMENT = '<?xml version="1.0" encoding="UTF-8"?><Response/>'

# The replies still running, so a task is not collected mid-answer.
_REPLIES: set[asyncio.Task] = set()


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(15.0))


def _account(credentials: dict[str, str]) -> tuple[str, str]:
    """(account id, auth token) — what every call to the platform is made as."""
    return str(credentials.get("account_sid") or ""), str(credentials.get("auth_token") or "")


def _refusal(response: httpx.Response) -> ChannelRefused:
    """What a rejected call may be recorded as — the status, never the body.

    The error body of a rejected call echoes the request back, and a request to this
    API carries the account id in its path. The code is what an operator can act on.
    """
    return ChannelRefused(f"the platform answered {response.status_code}")


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The account this token belongs to, by its own name — the test button (§A6.8)."""
    account_sid, auth_token = _account(credentials)
    response = await client.get(
        f"/{API_VERSION}/Accounts/{account_sid}.json", auth=(account_sid, auth_token)
    )
    if response.status_code >= 400:
        raise _refusal(response)
    body = response.json()
    named = body.get("friendly_name") if isinstance(body, dict) else None
    return str(named or account_sid)


def split_text(text: str, limit: int) -> list[str]:
    """One answer as the platform's text-sized pieces, cut between words.

    Never in the middle of a word, and never silently short: a truncated answer reads
    as a complete one, which is the failure this exists to prevent.
    """
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    current = ""
    for word in text.split(" "):
        remaining = word
        while len(remaining) > limit:
            # A single word longer than a whole text. Nothing to cut between, so it is
            # cut at the limit rather than dropped.
            if current:
                pieces.append(current)
                current = ""
            pieces.append(remaining[:limit])
            remaining = remaining[limit:]
        candidate = f"{current} {remaining}" if current else remaining
        if len(candidate) > limit:
            pieces.append(current)
            current = remaining
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One answer out to one number, as however many texts it takes."""
    account_sid, auth_token = _account(credentials)
    from_number = str(credentials.get("from_number") or "")
    for piece in split_text(text, MESSAGE_MAX):
        if not piece:
            continue
        response = await client.post(
            f"/{API_VERSION}/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={"From": from_number, "To": target, "Body": piece},
        )
        if response.status_code >= 400:
            raise _refusal(response)


# --- The signature --------------------------------------------------------------


def signature_for(auth_token: str, url: str, params: dict[str, str]) -> str:
    """The platform's own scheme: the URL it called, then the parameters in key order.

    Key and value concatenated with no separator, HMAC-SHA1 under the auth token,
    base64. SHA-1 is the platform's choice and not this product's; there is nothing to
    pick here, only a contract to keep.
    """
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(
        auth_token.encode(),
        payload.encode(),
        # The platform signs with SHA-1; this only checks what it produced.
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    auth_token: str, url: str, params: dict[str, str], header: str | None
) -> bool:
    if not header:
        return False
    return hmac.compare_digest(header, signature_for(auth_token, url, params))


def public_url(request: Request) -> str:
    """The address the platform called, which is the address it signed.

    Behind a reverse proxy the application server sees `http` and its own host while
    the platform called `https` and the public name. The forwarded headers are what
    close that gap; the first value of each is the original client's, per the header's
    own convention. Reconstructed rather than taken whole so nothing but the scheme
    and the host can be influenced from outside.
    """
    url = request.url
    forwarded_proto = request.headers.get("X-Forwarded-Proto")
    forwarded_host = request.headers.get("X-Forwarded-Host")
    scheme = forwarded_proto.split(",")[0].strip() if forwarded_proto else url.scheme
    host = forwarded_host.split(",")[0].strip() if forwarded_host else url.netloc
    return urlunsplit((scheme, host, url.path, url.query, ""))


# --- The conversation half ------------------------------------------------------


def message_text(event: dict[str, Any], identity: str) -> str | None:
    """The customer's words out of one delivery, or None when it is not one.

    None for a text from the number this channel speaks from — that is this
    installation hearing itself, and answering it is how a number texts itself
    forever — for a delivery with no sender, and for one with no words: a picture with
    no caption carries nothing to answer.
    """
    sender = str(event.get("From") or "").strip()
    if not sender or (identity and sender == identity):
        return None
    return str(event.get("Body") or "").strip() or None


async def _conversation_for(
    db: DbSession, channel: Channel, sender: str
) -> tuple[Conversation, bool]:
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == sender,
            Conversation.status == "open",
        )
    )
    if row is not None:
        return row, False
    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=sender,
        handling="ai",
        status="open",
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row, True


async def _store_line(
    db: DbSession, conversation: Conversation, *, speaker: str, text: str
) -> Message:
    line = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        ts_ms=position_ms(conversation.started_at),
        speaker=speaker,
        text=text,
        language=None,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


async def _announce(
    db: DbSession, channel: Channel, conversation: Conversation, message: Message, started: bool
) -> None:
    try:
        if started:
            await webhooks.queue(
                db,
                workspace_id=channel.workspace_id,
                event="conversation.started",
                data={
                    "conversation": conversation.external_id,
                    "channel": KIND,
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
            "could not queue webhooks for a text message",
            extra={"conversation_id": conversation.id},
        )


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


async def ingest(db: DbSession, channel: Channel, event: dict[str, Any]) -> int | None:
    """Store one delivery. Returns the stored line's id when a reply is due.

    Storage only, so the door can acknowledge inside its budget — the answer is
    `respond`'s job, on its own task and its own session.
    """
    from api.channels import health

    # A delivery that verified is the platform proving the address works; a door
    # channel has no poll to prove it with.
    await health.report_ok(db, channel)

    # The shown half only: the number this channel speaks from is plain in
    # `settings_json`, and the §B9 rule is that nothing decrypts on the inbound path
    # except the signature check itself.
    text = message_text(event, str(shown_of(channel).get("from_number") or ""))
    if text is None:
        return None
    sender = str(event["From"]).strip()

    # Milestone 4: the rules engine, before anything is stored.
    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[sender])
    if decision.action == "block":
        logger.info(
            "text message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await _conversation_for(db, channel, sender)
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    state = dict((conversation.state_json or {}).get(KIND) or {})
    message_sid = str(event.get("MessageSid") or "")
    if message_sid and state.get("last_message_sid") == message_sid:
        logger.info(
            "text delivery repeated, dropped",
            extra={"conversation_id": conversation.id},
        )
        return None
    conversation.state_json = {
        **(conversation.state_json or {}),
        KIND: {**state, "last_message_sid": message_sid},
    }

    line = await _store_line(db, conversation, speaker="caller", text=text)
    await _announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "text reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """Generate and deliver the answer to one stored line — the channels' contract:
    its own session, the takeover state read again, delivery before storage."""
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
        target = conversation.external_id or ""
        if not all(_account(credentials)) or not credentials.get("from_number") or not target:
            return

        async def took(taken: TakenMessage) -> None:
            await raise_notification(
                db,
                workspace_id=channel.workspace_id,
                category="review",
                message_key="message_taken",
                params={"name": taken.name, "reason": _preview(taken.reason)},
                needs_decision=True,
                primary_action="open_conversation",
                action_payload={"conversation_id": conversation.id},
                conversation_id=conversation.id,
            )

        try:
            provider = await llm.resolve_provider(db)
        except ConfigurationError:
            logger.exception("sms could not resolve a model", extra={"channel_id": channel_id})
            provider = None

        from api.routes.public_chat import _history

        history = await _history(db, conversation, line)

        import time

        reply_started = time.perf_counter()
        from api.agent_tools import toolset

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

        try:
            async with make_client() as client:
                await send_text(client, credentials, target, whole)
        except (ChannelRefused, httpx.HTTPError) as error:
            logger.warning(
                "text reply not delivered",
                extra={"conversation_id": conversation.id, "error": type(error).__name__},
            )
            return
        await _store_line(db, conversation, speaker="agent", text=whole)

        from api.channels import health

        # Rule 4: the whole journey, generation to delivery, measured per channel.
        health.note_reply(KIND, channel.id, (time.perf_counter() - reply_started) * 1000)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """The answer as its own task, kept in a set so it cannot be collected mid-reply."""
    task = asyncio.create_task(respond(sessionmaker, channel_id, message_id))
    _REPLIES.add(task)
    task.add_done_callback(_REPLIES.discard)


# --- The door -------------------------------------------------------------------


async def _parameters(request: Request) -> dict[str, str]:
    """What was signed alongside the address: the POST form, or nothing at all.

    A GET is signed over the URL alone — its query string is already part of that —
    so there is nothing further to concatenate.
    """
    if request.method != "POST":
        return {}
    raw = await request.body()
    return dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))


async def receive(db: DbSession, channel: Channel, request: Request) -> Response:
    """One delivery: verify, store, acknowledge, answer afterwards.

    Every failure raises, and the public route turns any raise into the one refusal
    every other reason gets. There is nothing to say to a stranger here.
    """
    auth_token = str(secrets_of(channel).get("auth_token") or "")
    if not auth_token:
        raise ChannelRefused("this channel has no auth token to verify with")

    params = await _parameters(request)
    if not verify_signature(
        auth_token, public_url(request), params, request.headers.get(SIGNATURE_HEADER)
    ):
        logger.info(
            "text delivery refused",
            extra={"reason": "bad signature", "channel_id": channel.id},
        )
        raise ChannelRefused("the signature did not check out")

    channel_id = channel.id
    needs_reply = await ingest(db, channel, params) if params else None
    if needs_reply is not None:
        schedule_reply(request.app.state.sessionmaker, channel_id, needs_reply)
    # An empty document, immediately: the answer travels by the API, not by this reply.
    return Response(content=ACKNOWLEDGEMENT, media_type="text/xml")
