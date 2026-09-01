"""Messenger and Instagram — two channels, one transport, §B13.

They are one product family on Meta's side and they are one module here. A Messenger
page and an Instagram Business account live inside the same customer-owned Meta
application, are messaged through the same `POST /me/messages` with a **page access
token** (Instagram DMs travel through the Facebook page the account is linked to),
and their webhooks are signed by the same app secret. What differs is a word: the
delivery says `object: "page"` for Messenger and `object: "instagram"` for
Instagram, and each channel row knows which one it is.

**Everything structural is WhatsApp's shape, deliberately.** The same one public door
per channel (a long random address, the `X-Hub-Signature-256` HMAC over the raw
body), the same acknowledge-first-answer-after split with the reply as its own task
re-reading the takeover state, the same dedup by Meta's message id in `state_json`,
and the same credentials-as-a-pair rule — token and secret are minted together and a
channel with one and not the other can speak but not listen.

**The echo guard is the rule these two add.** Subscribed pages are told about their
own outbound messages too; a sender id equal to the channel's own id is this
installation hearing itself, and answering it is how a page argues with itself
forever. WhatsApp never needed this; here it is load-bearing.

**The conversation is keyed by the platform's sender id.** A PSID or IGSID is a
page-scoped opaque identity — like a chat id, unlike the web resume handle — so it
may be shown, and it is all Meta gives without extra permissions.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent.config import ConfigurationError
from agent.reply import reply as generate_reply
from agent.tools import TakenMessage
from api import llm, webhooks
from api.config import get_settings
from api.conversations import position_ms
from api.db import session_scope
from api.models import Channel, Conversation, Message
from api.notifications import raise_notification

logger = logging.getLogger("api.meta_chat")

# The two kinds this module carries, and the `object` word each one's webhook says.
KINDS = ("messenger", "instagram")
OBJECT_FOR_KIND = {"messenger": "page", "instagram": "instagram"}

PREVIEW_MAX = 80

# Messenger and Instagram refuse text over 2000 characters - half WhatsApp's room.
MESSAGE_MAX = 2000

# The replies still running, so the tasks are not collected mid-answer.
_REPLIES: set[asyncio.Task] = set()


class MetaChatError(Exception):
    """The Graph API said no, with Meta's own message where it gave one."""


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=get_settings().meta_api_base, timeout=httpx.Timeout(15.0))


def credentials_for(channel: Channel) -> tuple[str, str] | None:
    """(page access token, app secret), or None while the card is incomplete."""
    if not channel.credentials_encrypted:
        return None
    try:
        stored = json.loads(channel.credentials_encrypted)
    except ValueError:
        return None
    token = str(stored.get("access_token") or "")
    secret = str(stored.get("app_secret") or "")
    if not token or not secret:
        return None
    return token, secret


def store_credentials(channel: Channel, *, access_token: str, app_secret: str) -> None:
    channel.credentials_encrypted = json.dumps(
        {"access_token": access_token, "app_secret": app_secret}
    )


def own_id(channel: Channel) -> str:
    """The page id (Messenger) or Instagram account id - the echo guard's anchor."""
    return str((channel.settings_json or {}).get("account_id") or "")


def is_ready(channel: Channel) -> bool:
    return bool(credentials_for(channel) and own_id(channel))


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


async def _api_error(response: httpx.Response) -> MetaChatError:
    try:
        detail = response.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    return MetaChatError(f"{response.status_code}: {detail or 'no detail'}")


async def send_text(
    client: httpx.AsyncClient, token: str, recipient_id: str, text: str
) -> None:
    """One message out, split on the platform's ceiling.

    `/me/messages` with the page token - `me` resolves to the page, and Instagram
    DMs go through the linked page the same way. `messaging_type: RESPONSE` says
    this answers an inbound message, which is the only kind this product sends.
    """
    for start in range(0, len(text) or 1, MESSAGE_MAX):
        response = await client.post(
            "/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "recipient": {"id": recipient_id},
                "message": {"text": text[start : start + MESSAGE_MAX]},
                "messaging_type": "RESPONSE",
            },
        )
        if response.status_code >= 400:
            raise await _api_error(response)


async def fetch_account(
    client: httpx.AsyncClient, token: str, account_id: str, kind: str
) -> dict[str, Any]:
    """Who this page or account is — the test-connection call (§A6.8)."""
    fields = "id,name" if kind == "messenger" else "id,username"
    response = await client.get(
        f"/{account_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": fields},
    )
    if response.status_code >= 400:
        raise await _api_error(response)
    body = response.json()
    return body if isinstance(body, dict) else {}


# --- The conversation half -----------------------------------------------------


def _events_of(payload: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """The text messages out of one delivery, in arrival order.

    Postbacks, read receipts and deliveries ride the same `messaging` array and are
    not conversation content; attachments are skipped whole rather than half-stored.
    """
    if payload.get("object") != OBJECT_FOR_KIND[kind]:
        return []
    found: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for event in entry.get("messaging") or []:
            message = event.get("message") or {}
            if message.get("text") and (event.get("sender") or {}).get("id"):
                found.append(event)
    return found


async def _conversation_for(
    db: DbSession, channel: Channel, sender_id: str
) -> tuple[Conversation, bool]:
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == sender_id,
            Conversation.status == "open",
        )
    )
    if row is not None:
        return row, False
    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=sender_id,
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
            "could not queue webhooks for a meta message",
            extra={"conversation_id": conversation.id},
        )


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


async def ingest(db: DbSession, channel: Channel, payload: dict[str, Any]) -> list[int]:
    """Store what one delivery carries. Returns ids of lines needing a reply.

    Storage only - the answer is `respond`'s job, after the 200. The echo guard
    lives here: a sender id equal to the channel's own id is this installation
    hearing itself, and it is dropped before it can become a conversation.
    """
    from api.channels import health

    # A signed delivery that reached this line is the platform proving the link
    # works - the webhook channels have no poll to prove it with.
    await health.report_ok(db, channel)

    needing_reply: list[int] = []
    self_id = own_id(channel)
    for event in _events_of(payload, channel.kind):
        sender_id = str(event["sender"]["id"])
        if sender_id == self_id:
            continue
        mid = str((event.get("message") or {}).get("mid") or "")
        body = str((event.get("message") or {}).get("text") or "").strip()
        if not body:
            continue

        conversation, started = await _conversation_for(db, channel, sender_id)
        state = dict((conversation.state_json or {}).get("meta") or {})
        if mid and state.get("last_mid") == mid:
            logger.info(
                "meta delivery repeated, dropped",
                extra={"conversation_id": conversation.id},
            )
            continue
        conversation.state_json = {
            **(conversation.state_json or {}),
            "meta": {**state, "last_mid": mid},
        }

        line = await _store_line(db, conversation, speaker="caller", text=body)
        await _announce(db, channel, conversation, line, started)

        if conversation.handling == "human":
            logger.info(
                "meta reply withheld, a person has the thread",
                extra={"conversation_id": conversation.id},
            )
            continue
        needing_reply.append(line.id)
    return needing_reply


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """Generate and deliver the answer to one stored line - WhatsApp's contract:
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
        credentials = credentials_for(channel)
        if credentials is None:
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
            logger.exception(
                "meta chat could not resolve a model", extra={"channel_id": channel_id}
            )
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
                await send_text(client, credentials[0], conversation.external_id or "", whole)
        except (MetaChatError, httpx.HTTPError) as error:
            logger.warning(
                "meta reply not delivered",
                extra={"conversation_id": conversation.id, "error": str(error)[:200]},
            )
            return
        await _store_line(db, conversation, speaker="agent", text=whole)

        from api.channels import health

        # Rule 4: the whole journey, generation to delivery, measured per channel.
        health.note_reply(
            channel.kind, channel.id, (time.perf_counter() - reply_started) * 1000
        )


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """The answer as its own task, kept in a set so it cannot be collected mid-reply."""
    task = asyncio.create_task(respond(sessionmaker, channel_id, message_id))
    _REPLIES.add(task)
    task.add_done_callback(_REPLIES.discard)
