"""The Telegram transport — the first platform channel, per §B13's own ordering.

Telegram comes first because it is the one platform channel with no review queue: a
customer makes a bot with @BotFather, pastes the token, and the channel is live. The
token is theirs, stored encrypted on their channel row (§B9.2) — Tel-Agent never holds
a shared platform application.

**Long polling, not webhooks, and that is a deployment decision.** A Telegram webhook
needs a public HTTPS address, and the supported deployment is a process on loopback
behind a LAN (§B9, G3) — an installation that must open a port to the internet before
its first message would contradict the reason this product self-hosts. `getUpdates`
works from behind any NAT with nothing exposed. A webhook mode can come later for
installations that already terminate TLS publicly; it would slot in beside this file,
not replace it.

**One dedicated loop, not a scheduler beat.** The job runner ticks every thirty
seconds, which is a fine cadence for cleanups and a terrible one for a conversation.
This loop long-polls, so a message is usually picked up within a second of being sent
— and it is one loop for the whole installation, walking every active Telegram
channel, so two workspaces with a bot each do not need two of anything.

**The conversation rules are the web channel's, not new ones.** A chat id keys the
thread through `external_id` — on Telegram that id *is* the identity, like a caller's
number, so unlike the web handle it may be shown (`_HANDLE_IS_A_SECRET` lists `web`
alone, on purpose). A taken-over thread gets no generated reply, same as the widget's
stream staying silent; the colleague's reply reaches Telegram directly from the reply
endpoint. The model is handed the same history, whispers as notes included, because
the history builder is imported rather than rewritten.
"""

from __future__ import annotations

import asyncio
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

logger = logging.getLogger("api.telegram")

# Telegram holds the request open until something arrives or this many seconds pass.
# Used when one channel is active; with several, each gets a short poll so no channel
# waits half a minute behind another's silence.
LONG_POLL_SECONDS = 20
SHORT_POLL_SECONDS = 1

# Between iterations when there is nothing to poll at all, so an installation with no
# Telegram channel costs one SELECT every few seconds and no network at all.
IDLE_SLEEP_SECONDS = 5.0

# Telegram refuses messages over 4096 characters. Long replies are split on that
# boundary rather than truncated: a cut-off answer reads as a fault, a second bubble
# reads as a long answer.
MESSAGE_MAX = 4096

# What the tray shows of a taken message - the same trim the web channel applies.
PREVIEW_MAX = 80


class TelegramError(Exception):
    """The Bot API said no, with Telegram's own description as the message."""


async def api_call(
    client: httpx.AsyncClient, token: str, method: str, payload: dict[str, Any]
) -> Any:
    """One Bot API call. Raises `TelegramError` on an `ok: false` answer.

    The token rides in the URL because that is the Bot API's shape. It is never
    logged: the URL is built here and handed straight to the client, and every log
    line below names the method and the channel, not the address.
    """
    response = await client.post(f"/bot{token}/{method}", json=payload)
    body = response.json()
    if not isinstance(body, dict) or not body.get("ok"):
        description = (
            body.get("description", "no description") if isinstance(body, dict) else "not JSON"
        )
        raise TelegramError(f"{method}: {description}")
    return body.get("result")


def make_client() -> httpx.AsyncClient:
    """A client against the configured Bot API base.

    The base is a setting so tests and development can stand in for Telegram; nobody
    changes it in production. The read timeout leaves room for a long poll to come
    back empty rather than reading its patience as an outage.
    """
    return httpx.AsyncClient(
        base_url=get_settings().telegram_api_base,
        timeout=httpx.Timeout(10.0, read=LONG_POLL_SECONDS + 15.0),
    )


async def get_me(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    """Who this token is — the test-connection call, proving the link rather than
    claiming it (§A6.8)."""
    return await api_call(client, token, "getMe", {})


def _chunks(text: str) -> list[str]:
    return [text[i : i + MESSAGE_MAX] for i in range(0, len(text), MESSAGE_MAX)] or [""]


async def send_text(
    client: httpx.AsyncClient, token: str, chat_id: int | str, text: str
) -> None:
    for piece in _chunks(text):
        await api_call(client, token, "sendMessage", {"chat_id": chat_id, "text": piece})


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


async def _conversation_for(
    db: DbSession, channel: Channel, chat_id: str
) -> tuple[Conversation, bool]:
    """The open thread for this chat, or a new one. Same shape as the widget's."""
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == chat_id,
            Conversation.status == "open",
        )
    )
    if row is not None:
        return row, False

    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=chat_id,
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
        # Null on a text channel is §B5's own signal that the line was typed.
        language=None,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


async def _announce(
    db: DbSession, channel: Channel, conversation: Conversation, message: Message, started: bool
) -> None:
    """The same webhooks the web channel queues, so a receiver cannot tell transports
    apart — which is the point of §B13's "a different transport" being the *only*
    difference."""
    try:
        if started:
            await webhooks.queue(
                db,
                workspace_id=channel.workspace_id,
                event="conversation.started",
                data={
                    "conversation": conversation.external_id,
                    "channel": "telegram",
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
            "could not queue webhooks for a telegram message",
            extra={"conversation_id": conversation.id},
        )


async def _reply_and_send(
    db: DbSession,
    client: httpx.AsyncClient,
    token: str,
    channel: Channel,
    conversation: Conversation,
    incoming: Message,
) -> None:
    """Generate the agent's answer and carry it back.

    Whole, not streamed: the Bot API has no token stream to speak into, and §B13
    says the property that makes text channels easy is that the user waits. The
    typing indicator is what fills the wait.
    """

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
        # Half a configuration. The customer hears what an unconfigured installation
        # says; the operator sees the reason on the health screen - the same split
        # the widget's stream makes.
        logger.exception("telegram could not resolve a model", extra={"channel_id": channel.id})
        provider = None

    from api.routes.public_chat import _history

    history = await _history(db, conversation, incoming)

    try:
        await api_call(
            client,
            token,
            "sendChatAction",
            {"chat_id": conversation.external_id, "action": "typing"},
        )
    except (TelegramError, httpx.HTTPError):
        # Cosmetic. A typing bubble that could not be shown must not cost the answer.
        logger.debug("telegram typing action failed", extra={"channel_id": channel.id})

    import time

    started = time.perf_counter()
    from api.agent_tools import toolset
    from api.db import create_sessionmaker

    # §B7's tools, bound to this conversation. The factory is derived from this
    # session's own engine: the tools open their own short sessions mid-generation.
    tools = toolset(
        create_sessionmaker(db.bind),
        workspace_id=channel.workspace_id,
        conversation_id=conversation.id,
    )

    pieces: list[str] = []
    async for chunk in generate_reply(
        incoming.text, provider=provider, history=history, on_message_taken=took, tools=tools
    ):
        pieces.append(chunk)
    whole = "".join(pieces)
    if not whole:
        return

    # Sent before it is stored. The transcript is what somebody reads back to learn
    # what a customer was told, so a reply that failed to send must not sit in it
    # looking like one that arrived - the failure raises, the message is not stored,
    # and the next poll answers the same question again.
    await send_text(client, token, conversation.external_id or "", whole)
    await _store_line(db, conversation, speaker="agent", text=whole)

    from api.channels import health

    # Rule 4: the whole journey, generation to delivery, measured per channel.
    health.note_reply("telegram", channel.id, (time.perf_counter() - started) * 1000)


async def _handle_update(
    db: DbSession,
    client: httpx.AsyncClient,
    token: str,
    channel: Channel,
    update: dict[str, Any],
) -> None:
    message = update.get("message") or {}
    text = message.get("text")
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not text or chat_id is None:
        # Stickers, joins, edits, photos - conversation content this channel does not
        # carry yet. Skipped rather than half-stored: a transcript line reading
        # "[photo]" would be a promise the archive cannot keep.
        return

    conversation, started = await _conversation_for(db, channel, str(chat_id))
    line = await _store_line(db, conversation, speaker="caller", text=str(text))
    await _announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        # A colleague has the thread (§A6.7). The agent is silent here for the same
        # reason the widget's stream is: two voices answering one customer, with the
        # second one in the archive as something the business said.
        logger.info(
            "telegram reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return

    await _reply_and_send(db, client, token, channel, conversation, line)


async def _active_channels(db: DbSession) -> list[Channel]:
    from api.channels import health

    # The decrypt-safe loader: a row saved under a key this installation no longer
    # has is reported down and skipped, instead of taking the whole pass with it.
    return await health.usable_channels(db, ("telegram",))


async def poll_once(db: DbSession, client: httpx.AsyncClient) -> int:
    """One pass over every active Telegram channel. Returns updates handled.

    The offset is confirmed to Telegram by the *next* call carrying it, so it is
    advanced and committed before replies are generated: a crash mid-reply then loses
    one answer, not the whole conversation into a redelivery loop.
    """
    channels = await _active_channels(db)
    handled = 0
    timeout = LONG_POLL_SECONDS if len(channels) == 1 else SHORT_POLL_SECONDS

    for channel in channels:
        # Captured before anything can roll the session back: an expired ORM object
        # read for a log line raises from inside the except that wanted to log.
        channel_id = channel.id
        token = channel.credentials_encrypted
        if not token:
            continue
        offset = int((channel.settings_json or {}).get("poll_offset") or 0)
        try:
            updates = await api_call(
                client,
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": timeout,
                    "allowed_updates": ["message"],
                },
            )
        except (TelegramError, httpx.HTTPError) as error:
            # A wrong token, or Telegram unreachable. Logged, told to the health
            # registry (which raises the tray alert on the transition), and left for
            # the next pass - the loop must survive one channel's bad day.
            from api.channels import health

            await health.report_down(db, channel, detail=str(error))
            continue

        from api.channels import health

        await health.report_ok(db, channel)
        if not isinstance(updates, list) or not updates:
            continue

        channel.settings_json = {
            **(channel.settings_json or {}),
            "poll_offset": max(int(u.get("update_id", 0)) for u in updates) + 1,
        }
        await db.commit()

        for update in updates:
            try:
                await _handle_update(db, client, token, channel, update)
                handled += 1
            except (TelegramError, httpx.HTTPError):
                await db.rollback()
                logger.exception("telegram update failed", extra={"channel_id": channel_id})
                # The rollback expired every loaded object, and the next update in
                # this batch still needs the channel's columns.
                await db.refresh(channel)
    return handled


async def loop(sessionmaker: async_sessionmaker) -> None:
    """The transport loop, started and cancelled by the app lifespan.

    Its own loop rather than a scheduler task for the reason the module docstring
    gives, and wrapped the way the job loop is: one bad iteration must not end
    polling silently — a channel that stops answering with no error anywhere is the
    failure §B8 exists to prevent.
    """
    async with make_client() as client:
        while True:
            had_channels = False
            try:
                async with session_scope(sessionmaker) as db:
                    had_channels = bool(await _active_channels(db))
                    if had_channels:
                        await poll_once(db, client)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("telegram loop iteration failed")
            # A long poll already waited; an idle installation should not spin.
            await asyncio.sleep(0.5 if had_channels else IDLE_SLEEP_SECONDS)
