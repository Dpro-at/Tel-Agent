"""The Slack transport — Socket Mode, §B13.

Slack sits on the channel side of Rule 5's line for exactly one case: an outside
customer in a shared channel with a supplier is a route in; an internal workspace is
not. The transport serves that case and the direct message.

**Socket Mode, not the Events API, and that is the deployment story again.** The
Events API needs a public HTTPS callback; Socket Mode has Slack hold the connection
open while this installation dials out — like Telegram's poll and Discord's gateway,
nothing is exposed. The customer's own Slack app provides two tokens minted
together: the app-level token (`xapp-…`) that opens the socket, and the bot token
(`xoxb-…`) that speaks. They travel as a pair, like Meta's.

**Where the bot answers is the Discord policy with Slack's words.** A direct message
(`channel_type: im`) always answers; a channel message answers only as an
`app_mention`, with the mention stripped - a bot that replies to every line of a
busy channel is noise the workspace will uninstall. Anything carrying a `bot_id`, a
`subtype` (edits, joins, system lines) or this bot's own user id is skipped whole.

**Every envelope is acknowledged, whatever became of it.** Socket Mode redelivers
what was not acked; the ack says "received", not "answered", and the reply runs as
its own task so the socket stays responsive. A redelivery that arrives anyway is
dropped by the event id.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import websockets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent.config import ConfigurationError
from agent.reply import reply as generate_reply
from agent.tools import TakenMessage
from api import llm, routing, webhooks
from api.config import get_settings
from api.conversations import position_ms
from api.db import session_scope
from api.models import Channel, Conversation, Message
from api.notifications import raise_notification

logger = logging.getLogger("api.slack")

PREVIEW_MAX = 80

# Slack truncates around 4000 characters of text in a message.
MESSAGE_MAX = 4000

SUPERVISE_SECONDS = 15.0

_REPLIES: set[asyncio.Task] = set()


class SlackError(Exception):
    """The Web API said no, with Slack's own error word."""


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=get_settings().slack_api_base, timeout=httpx.Timeout(15.0)
    )


def credentials_for(channel: Channel) -> tuple[str, str] | None:
    """(app-level token, bot token), or None while the card is incomplete."""
    if not channel.credentials_encrypted:
        return None
    try:
        stored = json.loads(channel.credentials_encrypted)
    except ValueError:
        return None
    app_token = str(stored.get("app_token") or "")
    bot_token = str(stored.get("bot_token") or "")
    if not app_token or not bot_token:
        return None
    return app_token, bot_token


def store_credentials(channel: Channel, *, app_token: str, bot_token: str) -> None:
    channel.credentials_encrypted = json.dumps({"app_token": app_token, "bot_token": bot_token})


async def _call(
    client: httpx.AsyncClient, token: str, method: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """One Web API call. Slack answers 200 with `ok: false`, so the status is not
    the verdict - the body is."""
    response = await client.post(
        f"/{method}",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    try:
        body = response.json()
    except ValueError as error:
        raise SlackError(f"{method}: not JSON") from error
    if not isinstance(body, dict) or not body.get("ok"):
        raise SlackError(f"{method}: {body.get('error', 'no error word')}")
    return body


async def send_text(
    client: httpx.AsyncClient, bot_token: str, slack_channel: str, text: str
) -> None:
    for start in range(0, len(text) or 1, MESSAGE_MAX):
        await _call(
            client,
            bot_token,
            "chat.postMessage",
            {"channel": slack_channel, "text": text[start : start + MESSAGE_MAX]},
        )


async def auth_test(client: httpx.AsyncClient, bot_token: str) -> dict[str, Any]:
    """Who this bot token is — the test-connection call (§A6.8), and where the
    echo guard's own user id comes from."""
    return await _call(client, bot_token, "auth.test", {})


async def socket_url(client: httpx.AsyncClient, app_token: str) -> str:
    answer = await _call(client, app_token, "apps.connections.open", {})
    return str(answer.get("url") or "")


# --- The conversation half -----------------------------------------------------


def message_text(event: dict[str, Any], bot_user_id: str) -> str | None:
    """The customer's words out of one event, or None when it is not a customer.

    None for anything carrying a `bot_id` or a `subtype` (edits, joins, system
    lines), for this bot's own user, for channel talk that is not an `app_mention`,
    and for empty text. The mention is stripped: addressing, not content.
    """
    if event.get("bot_id") or event.get("subtype"):
        return None
    user = str(event.get("user") or "")
    if not user or user == bot_user_id:
        return None
    text = str(event.get("text") or "")
    kind = event.get("type")
    if kind == "app_mention":
        text = text.replace(f"<@{bot_user_id}>", "")
    elif kind == "message":
        if event.get("channel_type") != "im":
            return None
    else:
        return None
    return text.strip() or None


async def _conversation_for(
    db: DbSession, channel: Channel, user_id: str
) -> tuple[Conversation, bool]:
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == user_id,
            Conversation.status == "open",
        )
    )
    if row is not None:
        return row, False
    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=user_id,
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
                    "channel": "slack",
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
            "could not queue webhooks for a slack message",
            extra={"conversation_id": conversation.id},
        )


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


def reply_channel(conversation: Conversation) -> str:
    return str(((conversation.state_json or {}).get("slack") or {}).get("reply_channel") or "")


async def ingest(
    db: DbSession,
    channel: Channel,
    event: dict[str, Any],
    bot_user_id: str,
    event_id: str = "",
) -> int | None:
    """Store one event. Returns the stored line's id when a reply is due."""
    text = message_text(event, bot_user_id)
    if text is None:
        return None

    user_id = str(event["user"])
    # Milestone 4: the rules engine, before anything is stored.
    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[user_id])
    if decision.action == "block":
        logger.info(
            "slack message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await _conversation_for(db, channel, user_id)
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    state = dict((conversation.state_json or {}).get("slack") or {})
    marker = event_id or str(event.get("ts") or "")
    if marker and state.get("last_event_id") == marker:
        logger.info("slack event repeated, dropped", extra={"conversation_id": conversation.id})
        return None
    conversation.state_json = {
        **(conversation.state_json or {}),
        "slack": {
            **state,
            "last_event_id": marker,
            "reply_channel": str(event.get("channel") or ""),
        },
    }

    line = await _store_line(db, conversation, speaker="caller", text=text)
    await _announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "slack reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """The channels' contract: own session, takeover re-read, delivery before storage."""
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
        room = reply_channel(conversation)
        if credentials is None or not room:
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
                "slack could not resolve a model", extra={"channel_id": channel_id}
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
                await send_text(client, credentials[1], room, whole)
        except (SlackError, httpx.HTTPError) as error:
            logger.warning(
                "slack reply not delivered",
                extra={"conversation_id": conversation.id, "error": str(error)[:200]},
            )
            return
        await _store_line(db, conversation, speaker="agent", text=whole)

        from api.channels import health

        # Rule 4: the whole journey, generation to delivery, measured per channel.
        health.note_reply("slack", channel.id, (time.perf_counter() - reply_started) * 1000)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    task = asyncio.create_task(respond(sessionmaker, channel_id, message_id))
    _REPLIES.add(task)
    task.add_done_callback(_REPLIES.discard)


async def _report_state(
    sessionmaker: async_sessionmaker, channel_id: int, *, ok: bool, detail: str | None = None
) -> None:
    """Tell the health registry how this connection is doing, on its own session.

    The gateway task holds no request and no session; borrowing one for the report
    keeps the transition alert (Milestone 9) working from inside a dropped socket.
    """
    from api.channels import health

    async with session_scope(sessionmaker) as db:
        channel = await db.scalar(select(Channel).where(Channel.id == channel_id))
        if channel is None:
            return
        if ok:
            await health.report_ok(db, channel)
        else:
            await health.report_down(db, channel, detail=detail or "connection lost")


# --- The socket -----------------------------------------------------------------


async def _run_socket(
    sessionmaker: async_sessionmaker, channel_id: int, app_token: str, bot_token: str
) -> None:
    """One workspace's Socket Mode session.

    `apps.connections.open` hands out a one-use WebSocket address; every
    `events_api` envelope is acked immediately by its id, and a `disconnect`
    envelope is Slack asking politely for a fresh dial - the supervisor's retry is
    the answer. The bot's own user id comes from `auth.test` once per session.
    """
    async with make_client() as rest:
        url = await socket_url(rest, app_token)
        me = await auth_test(rest, bot_token)
    bot_user_id = str(me.get("user_id") or "")

    async with websockets.connect(url, max_size=2**22) as connection:
        logger.info("slack socket ready", extra={"channel_id": channel_id})
        await _report_state(sessionmaker, channel_id, ok=True)
        async for raw in connection:
            envelope = json.loads(raw)
            kind = envelope.get("type")
            if envelope.get("envelope_id"):
                # Acked first, whatever becomes of it: the ack says "received",
                # and an envelope held hostage to a slow model gets redelivered.
                await connection.send(json.dumps({"envelope_id": envelope["envelope_id"]}))
            if kind == "disconnect":
                return
            if kind != "events_api":
                continue

            payload = envelope.get("payload") or {}
            event = payload.get("event") or {}
            event_id = str(payload.get("event_id") or "")
            async with session_scope(sessionmaker) as db:
                channel = await db.scalar(
                    select(Channel).where(Channel.id == channel_id, Channel.status == "active")
                )
                if channel is None:
                    return
                needs_reply = await ingest(db, channel, event, bot_user_id, event_id)
            if needs_reply is not None:
                schedule_reply(sessionmaker, channel_id, needs_reply)


async def _connection(
    sessionmaker: async_sessionmaker, channel_id: int, app_token: str, bot_token: str
) -> None:
    backoff = 5.0
    while True:
        try:
            await _run_socket(sessionmaker, channel_id, app_token, bot_token)
            backoff = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "slack socket dropped",
                extra={"channel_id": channel_id, "error": str(error)[:200]},
            )
            await _report_state(sessionmaker, channel_id, ok=False, detail=str(error))
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300.0)


async def loop(sessionmaker: async_sessionmaker) -> None:
    """The supervisor: one socket per active Slack channel — Discord's shape."""
    running: dict[int, tuple[str, asyncio.Task]] = {}
    try:
        while True:
            try:
                async with session_scope(sessionmaker) as db:
                    from api.channels import health

                    rows = await health.usable_channels(db, ("slack",))
                    wanted = {
                        row.id: row.credentials_encrypted
                        for row in rows
                        if row.credentials_encrypted
                    }

                for channel_id, (stored, task) in list(running.items()):
                    if channel_id not in wanted or wanted[channel_id] != stored or task.done():
                        task.cancel()
                        running.pop(channel_id)
                for channel_id, stored in wanted.items():
                    if channel_id not in running:
                        pair = json.loads(stored) if stored else {}
                        running[channel_id] = (
                            stored,
                            asyncio.create_task(
                                _connection(
                                    sessionmaker,
                                    channel_id,
                                    str(pair.get("app_token") or ""),
                                    str(pair.get("bot_token") or ""),
                                )
                            ),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("slack supervisor iteration failed")
            await asyncio.sleep(SUPERVISE_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
