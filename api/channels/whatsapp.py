"""The WhatsApp transport — the first platform channel with a review queue, §B13.

The customer connects their own Meta application: a permanent access token, the phone
number id it sends as, and the app secret that signs every webhook Meta delivers.
Tel-Agent never holds a shared platform application — one shared app would put every
installation behind one rate limit and make one policy violation everybody's outage.

**Webhooks, because the platform offers nothing else.** Telegram and email could poll
from behind a NAT; the Cloud API has no inbox to poll — Meta pushes, or nothing
arrives. So this is the first channel that needs the installation reachable over
public HTTPS, which is §B9's documented reverse-proxy path, not a new requirement
invented here. What guards the endpoint is what guards the web widget, plus Meta's
own half: a long random address that is never chosen, and the `X-Hub-Signature-256`
HMAC over the raw body with the app secret, refused identically whether the address
is wrong, the channel is off, or the signature does not match.

**Acknowledged first, answered after.** Meta retries a webhook that does not answer
promptly, and a model reply takes as long as it takes — so the delivery is stored and
200 goes back while the reply runs as its own task on its own session. A retry that
does arrive anyway is dropped by the message id Meta stamps on every delivery, kept
in `state_json`.

**The conversation is keyed by the customer's number.** `wa_id` is E.164 without the
plus; stored with it, so the same identity matches the phonebook the way a caller's
number will — an identity like a number, shown as one.
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

logger = logging.getLogger("api.whatsapp")

PREVIEW_MAX = 80

# WhatsApp refuses text bodies over 4096 characters, like Telegram.
MESSAGE_MAX = 4096

# The replies still running, so the tasks are not collected mid-answer. Entries
# remove themselves when done.
_REPLIES: set[asyncio.Task] = set()


class WhatsAppError(Exception):
    """The Graph API said no, with Meta's own message where it gave one."""


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=get_settings().whatsapp_api_base, timeout=httpx.Timeout(15.0)
    )


def credentials_for(channel: Channel) -> tuple[str, str] | None:
    """(access token, app secret), or None while the card is incomplete.

    Both live in the one encrypted column as a JSON pair: they are minted together
    in the same Meta application, and a row with one and not the other is a channel
    that can speak but not listen, or listen but not prove who spoke.
    """
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


def is_ready(channel: Channel) -> bool:
    """Complete enough to switch on: both secrets and the number id."""
    return bool(
        credentials_for(channel) and (channel.settings_json or {}).get("phone_number_id")
    )


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """Meta's HMAC over the raw body. Compared constant-time, absent means refused."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.removeprefix("sha256="), expected)


async def _api_error(response: httpx.Response) -> WhatsAppError:
    try:
        detail = response.json().get("error", {}).get("message", "")
    except ValueError:
        detail = ""
    return WhatsAppError(f"{response.status_code}: {detail or 'no detail'}")


async def send_text(
    client: httpx.AsyncClient, token: str, phone_number_id: str, to: str, text: str
) -> None:
    for start in range(0, len(text) or 1, MESSAGE_MAX):
        response = await client.post(
            f"/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": to.removeprefix("+"),
                "type": "text",
                "text": {"body": text[start : start + MESSAGE_MAX]},
            },
        )
        if response.status_code >= 400:
            raise await _api_error(response)


async def fetch_number(
    client: httpx.AsyncClient, token: str, phone_number_id: str
) -> dict[str, Any]:
    """Who this number is — the test-connection call, proving the link (§A6.8)."""
    response = await client.get(
        f"/{phone_number_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"fields": "display_phone_number,verified_name"},
    )
    if response.status_code >= 400:
        raise await _api_error(response)
    body = response.json()
    return body if isinstance(body, dict) else {}


# --- The conversation half -----------------------------------------------------


def _texts_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The text messages out of one webhook delivery, in arrival order.

    Statuses (sent, delivered, read) ride the same webhook and are not conversation
    content; media is skipped whole rather than half-stored as `[image]`.
    """
    found: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for message in (change.get("value") or {}).get("messages") or []:
                if message.get("type") == "text" and message.get("from"):
                    found.append(message)
    return found


async def _conversation_for(
    db: DbSession, channel: Channel, wa_id: str
) -> tuple[Conversation, bool]:
    # With the plus, so the identity is the E.164 the phonebook is keyed by.
    number = "+" + wa_id.removeprefix("+")
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == number,
            Conversation.status == "open",
        )
    )
    if row is not None:
        return row, False
    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=number,
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
                    "channel": "whatsapp",
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
            "could not queue webhooks for a whatsapp message",
            extra={"conversation_id": conversation.id},
        )


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


async def ingest(db: DbSession, channel: Channel, payload: dict[str, Any]) -> list[int]:
    """Store what one webhook delivery carries. Returns ids of lines needing a reply.

    Storage only — the answer is `respond`'s job, on its own session, after the 200
    has gone back to Meta. A retry of a delivery already ingested is dropped by the
    message id Meta stamps on it, kept per conversation in `state_json`.
    """
    from api.channels import health

    # A signed delivery that reached this line is the platform proving the link
    # works - the webhook channels have no poll to prove it with.
    await health.report_ok(db, channel)

    needing_reply: list[int] = []
    for message in _texts_of(payload):
        wamid = str(message.get("id") or "")
        body = str(((message.get("text") or {}).get("body")) or "").strip()
        if not body:
            continue

        conversation, started = await _conversation_for(db, channel, str(message["from"]))
        state = dict((conversation.state_json or {}).get("whatsapp") or {})
        if wamid and state.get("last_wamid") == wamid:
            logger.info(
                "whatsapp delivery repeated, dropped",
                extra={"conversation_id": conversation.id},
            )
            continue
        conversation.state_json = {
            **(conversation.state_json or {}),
            "whatsapp": {**state, "last_wamid": wamid},
        }

        line = await _store_line(db, conversation, speaker="caller", text=body)
        await _announce(db, channel, conversation, line, started)

        if conversation.handling == "human":
            # A colleague has the thread (§A6.7); the same silence as every channel.
            logger.info(
                "whatsapp reply withheld, a person has the thread",
                extra={"conversation_id": conversation.id},
            )
            continue
        needing_reply.append(line.id)
    return needing_reply


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """Generate and deliver the answer to one stored line.

    Its own session, because the webhook request that stored the line has already
    been answered and its session returned. The takeover state is read again here:
    a colleague who pressed the button between the 200 and this task keeps the
    silence they asked for.
    """
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
        phone_number_id = str((channel.settings_json or {}).get("phone_number_id") or "")
        if credentials is None or not phone_number_id:
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
                "whatsapp could not resolve a model", extra={"channel_id": channel_id}
            )
            provider = None

        from api.routes.public_chat import _history

        history = await _history(db, conversation, line)

        import time

        reply_started = time.perf_counter()
        pieces: list[str] = []
        async for chunk in generate_reply(
            line.text, provider=provider, history=history, on_message_taken=took
        ):
            pieces.append(chunk)
        whole = "".join(pieces)
        if not whole:
            return

        # Sent before it is stored - the push-transport rule: a stored line that
        # never reached the customer would be a transcript lying about what the
        # business said.
        try:
            async with make_client() as client:
                await send_text(
                    client,
                    credentials[0],
                    phone_number_id,
                    conversation.external_id or "",
                    whole,
                )
        except (WhatsAppError, httpx.HTTPError) as error:
            logger.warning(
                "whatsapp reply not delivered",
                extra={"conversation_id": conversation.id, "error": str(error)[:200]},
            )
            return
        await _store_line(db, conversation, speaker="agent", text=whole)

        from api.channels import health

        # Rule 4: the whole journey, generation to delivery, measured per channel.
        health.note_reply("whatsapp", channel.id, (time.perf_counter() - reply_started) * 1000)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """The answer as its own task, so the webhook can be acknowledged now.

    Kept in a set until done: a bare `create_task` is garbage-collectable mid-reply,
    which would be a customer's answer disappearing with no error anywhere.
    """
    task = asyncio.create_task(respond(sessionmaker, channel_id, message_id))
    _REPLIES.add(task)
    task.add_done_callback(_REPLIES.discard)
