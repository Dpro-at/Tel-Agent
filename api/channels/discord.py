"""The Discord transport — a bot on the gateway, §B13.

The customer makes a bot in their own Discord developer portal and pastes its token;
Tel-Agent never holds a shared application. Inbound is the **gateway WebSocket**,
because Discord offers nothing else for conversational messages — the REST API can
send but not listen, and the interactions webhook carries slash commands and forms,
not a customer typing. The gateway needs no public address at all, which keeps the
LAN deployment story intact: this installation dials out, like Telegram's long poll.

**Where the bot answers is a policy, not an accident.** In a direct message it always
answers — a DM to the business's bot is a customer walking up to the desk. In a
server channel it answers only when mentioned, because a bot that replies to every
line of a busy channel is noise the server will kick. The mention is stripped before
the text enters the record; the customer said "book me Tuesday", not
"@Tel-Agent book me Tuesday".

**Bots are never customers.** Any message whose author is a bot — this one or any
other — is skipped whole. Two bots answering each other is the email auto-reply loop
with better latency, and `author.bot` is the platform's own word for it.

**The conversation is keyed by the person, the reply goes to the room.** One Discord
user is one conversation wherever they speak from; which channel to answer into is
whatever they last spoke in, kept in `state_json` — the same split email makes
between the correspondent and the thread headers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx
import websockets
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

logger = logging.getLogger("api.discord")

PREVIEW_MAX = 80

# Discord refuses message content over 2000 characters.
MESSAGE_MAX = 2000

# GUILDS is what makes the gateway session valid, GUILD_MESSAGES and DIRECT_MESSAGES
# are the two rooms this channel listens in, and MESSAGE_CONTENT is the privileged
# intent without which every message body arrives empty - the card's copy tells the
# operator to switch it on in the developer portal.
INTENTS = 1 | (1 << 9) | (1 << 12) | (1 << 15)

# How often the supervisor reconciles running connections against the channels table.
SUPERVISE_SECONDS = 15.0

_REPLIES: set[asyncio.Task] = set()


class DiscordError(Exception):
    """The REST API said no, with Discord's own message where it gave one."""


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=get_settings().discord_api_base, timeout=httpx.Timeout(15.0)
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}"}


async def _api_error(response: httpx.Response) -> DiscordError:
    try:
        detail = response.json().get("message", "")
    except ValueError:
        detail = ""
    return DiscordError(f"{response.status_code}: {detail or 'no detail'}")


async def send_text(client: httpx.AsyncClient, token: str, channel_id: str, text: str) -> None:
    for start in range(0, len(text) or 1, MESSAGE_MAX):
        response = await client.post(
            f"/channels/{channel_id}/messages",
            headers=_headers(token),
            json={"content": text[start : start + MESSAGE_MAX]},
        )
        if response.status_code >= 400:
            raise await _api_error(response)


async def fetch_me(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    """Who this token is — the test-connection call, proving the link (§A6.8)."""
    response = await client.get("/users/@me", headers=_headers(token))
    if response.status_code >= 400:
        raise await _api_error(response)
    body = response.json()
    return body if isinstance(body, dict) else {}


async def gateway_url(client: httpx.AsyncClient, token: str) -> str:
    response = await client.get("/gateway/bot", headers=_headers(token))
    if response.status_code >= 400:
        raise await _api_error(response)
    return str(response.json().get("url") or "")


# --- The conversation half -----------------------------------------------------


def message_text(event: dict[str, Any], bot_user_id: str) -> str | None:
    """The customer's words out of one MESSAGE_CREATE, or None when it is not one.

    None for a bot author (never a customer), for a guild message that does not
    mention this bot (a busy channel is not a conversation with us), and for empty
    content (an attachment-only message, or the MESSAGE_CONTENT intent left off).
    The mention itself is stripped: it is addressing, not content.
    """
    author = event.get("author") or {}
    if author.get("bot") or not author.get("id"):
        return None
    content = str(event.get("content") or "")
    if event.get("guild_id"):
        mentioned = any(
            str(user.get("id")) == bot_user_id for user in event.get("mentions") or []
        )
        if not mentioned:
            return None
        for form in (f"<@{bot_user_id}>", f"<@!{bot_user_id}>"):
            content = content.replace(form, "")
    return content.strip() or None


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
                    "channel": "discord",
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
            "could not queue webhooks for a discord message",
            extra={"conversation_id": conversation.id},
        )


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


def reply_channel(conversation: Conversation) -> str:
    return str(
        ((conversation.state_json or {}).get("discord") or {}).get("reply_channel") or ""
    )


async def ingest(
    db: DbSession, channel: Channel, event: dict[str, Any], bot_user_id: str
) -> int | None:
    """Store one MESSAGE_CREATE. Returns the stored line's id when a reply is due.

    Storage only, so the gateway loop stays free to answer heartbeats - the reply
    runs as its own task. A redelivered event is dropped by Discord's message id.
    """
    text = message_text(event, bot_user_id)
    if text is None:
        return None

    user_id = str(event["author"]["id"])
    conversation, started = await _conversation_for(db, channel, user_id)

    state = dict((conversation.state_json or {}).get("discord") or {})
    event_id = str(event.get("id") or "")
    if event_id and state.get("last_event_id") == event_id:
        logger.info(
            "discord event repeated, dropped", extra={"conversation_id": conversation.id}
        )
        return None
    conversation.state_json = {
        **(conversation.state_json or {}),
        "discord": {
            **state,
            "last_event_id": event_id,
            # The room to answer into: wherever the person last spoke.
            "reply_channel": str(event.get("channel_id") or ""),
        },
    }

    line = await _store_line(db, conversation, speaker="caller", text=text)
    await _announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "discord reply withheld, a person has the thread",
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
        token = channel.credentials_encrypted
        room = reply_channel(conversation)
        if not token or not room:
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
                "discord could not resolve a model", extra={"channel_id": channel_id}
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
                await send_text(client, token, room, whole)
        except (DiscordError, httpx.HTTPError) as error:
            logger.warning(
                "discord reply not delivered",
                extra={"conversation_id": conversation.id, "error": str(error)[:200]},
            )
            return
        await _store_line(db, conversation, speaker="agent", text=whole)

        from api.channels import health

        # Rule 4: the whole journey, generation to delivery, measured per channel.
        health.note_reply("discord", channel.id, (time.perf_counter() - reply_started) * 1000)


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


# --- The gateway ----------------------------------------------------------------


async def _run_gateway(sessionmaker: async_sessionmaker, channel_id: int, token: str) -> None:
    """One bot's gateway session: identify, heartbeat, hand MESSAGE_CREATE to ingest.

    The protocol here is the minimum that stays connected: HELLO sets the heartbeat
    interval, IDENTIFY names the bot and its intents, READY carries the bot's own
    user id (the echo guard's anchor and the mention filter's), opcode 1 demands an
    immediate heartbeat, and opcodes 7 and 9 both mean "connect again". Resume is
    deliberately not implemented - a missed message on reconnect costs one customer
    one repeat, and a session-resume implementation costs every future reader.
    """
    async with make_client() as rest:
        url = await gateway_url(rest, token)
    async with websockets.connect(f"{url}?v=10&encoding=json", max_size=2**22) as connection:
        hello = json.loads(await connection.recv())
        interval = float(hello.get("d", {}).get("heartbeat_interval", 41250)) / 1000.0
        sequence: int | None = None

        await connection.send(
            json.dumps(
                {
                    "op": 2,
                    "d": {
                        "token": token,
                        "intents": INTENTS,
                        "properties": {
                            "os": "linux",
                            "browser": "telagent",
                            "device": "telagent",
                        },
                    },
                }
            )
        )

        bot_user_id = ""
        # Jittered per the gateway's own instruction, so a fleet of restarts does
        # not heartbeat in unison. Scheduling, not secrecy - the PRNG is fine.
        next_beat = asyncio.get_running_loop().time() + interval * random.random()  # noqa: S311

        while True:
            timeout = max(0.1, next_beat - asyncio.get_running_loop().time())
            try:
                raw = await asyncio.wait_for(connection.recv(), timeout=timeout)
            except TimeoutError:
                await connection.send(json.dumps({"op": 1, "d": sequence}))
                next_beat = asyncio.get_running_loop().time() + interval
                continue

            frame = json.loads(raw)
            op = frame.get("op")
            if frame.get("s") is not None:
                sequence = frame["s"]

            if op == 1:
                await connection.send(json.dumps({"op": 1, "d": sequence}))
            elif op in (7, 9):
                # Reconnect, or a session the gateway no longer recognises. The
                # supervisor's retry loop is the reconnect.
                return
            elif op == 0:
                kind = frame.get("t")
                data = frame.get("d") or {}
                if kind == "READY":
                    bot_user_id = str((data.get("user") or {}).get("id") or "")
                    logger.info("discord gateway ready", extra={"channel_id": channel_id})
                    await _report_state(sessionmaker, channel_id, ok=True)
                elif kind == "MESSAGE_CREATE" and bot_user_id:
                    async with session_scope(sessionmaker) as db:
                        channel = await db.scalar(
                            select(Channel).where(
                                Channel.id == channel_id, Channel.status == "active"
                            )
                        )
                        if channel is None:
                            return
                        needs_reply = await ingest(db, channel, data, bot_user_id)
                    if needs_reply is not None:
                        schedule_reply(sessionmaker, channel_id, needs_reply)


async def _connection(sessionmaker: async_sessionmaker, channel_id: int, token: str) -> None:
    """One channel's connection, forever: run the gateway, and on any failure wait
    and dial again. The supervisor cancels this when the channel goes away."""
    backoff = 5.0
    while True:
        try:
            await _run_gateway(sessionmaker, channel_id, token)
            backoff = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "discord gateway dropped",
                extra={"channel_id": channel_id, "error": str(error)[:200]},
            )
            await _report_state(sessionmaker, channel_id, ok=False, detail=str(error))
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300.0)


async def loop(sessionmaker: async_sessionmaker) -> None:
    """The supervisor: one gateway connection per active Discord channel.

    Every beat it reconciles what runs against what the table says - a channel
    switched on gets a connection, one switched off or re-tokened gets its
    connection cancelled and redialled. The table is the truth; the tasks follow it.
    """
    running: dict[int, tuple[str, asyncio.Task]] = {}
    try:
        while True:
            try:
                async with session_scope(sessionmaker) as db:
                    from api.channels import health

                    rows = await health.usable_channels(db, ("discord",))
                    wanted = {
                        row.id: row.credentials_encrypted
                        for row in rows
                        if row.credentials_encrypted
                    }

                for channel_id, (token, task) in list(running.items()):
                    if channel_id not in wanted or wanted[channel_id] != token or task.done():
                        task.cancel()
                        running.pop(channel_id)
                for channel_id, token in wanted.items():
                    if channel_id not in running:
                        running[channel_id] = (
                            token,
                            asyncio.create_task(_connection(sessionmaker, channel_id, token)),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("discord supervisor iteration failed")
            await asyncio.sleep(SUPERVISE_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
