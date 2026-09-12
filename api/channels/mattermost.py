"""The Mattermost transport — a bot account on the WebSocket gateway, §B13.

The customer makes a bot account in their own Mattermost installation and pastes its
access token; Tel-Agent never holds a shared platform application. Inbound is the
server's **WebSocket event stream** (`/api/v4/websocket`), and outbound is the
REST API (`/api/v4/posts`). The gateway needs no public address at all, which keeps
the self-hosted LAN deployment story intact: this installation dials out.

**Where the bot answers is a policy, not an accident.** In a direct message it always
answers — a DM to the business's bot is a customer walking up to the desk. In a
public or private channel it answers only when mentioned (`@<bot_username>`),
because a bot that replies to every line of a busy channel is noise the channel will
kick. The mention is stripped before the text enters the record.

**Bots are never customers.** Any message whose author is a bot or whose post has
`props.from_bot == "true"` is skipped whole. Two bots answering each other is an
infinite loop, and `from_bot` is the platform's own word for it.

**Replies keep the thread root.** A post in a thread carries a `root_id`; a top-level
post's id becomes the `root_id` for answers, so conversations stay grouped in threads
on the Mattermost side.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import httpx
import websockets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.channels import generic
from api.channels.generic import ChannelRefused
from api.channels.setup import Field, Setup
from api.conversations import position_ms
from api.db import session_scope
from api.models import Channel, Conversation, Message

logger = logging.getLogger("api.mattermost")

# Mattermost default post character limit.
MESSAGE_MAX = 4000

# How often the supervisor reconciles running connections against the channels table.
SUPERVISE_SECONDS = 15.0

KIND = "mattermost"
INBOUND = "dial_out"

SETUP = Setup(
    kind=KIND,
    title="Mattermost",
    note="A bot account in your own Mattermost server answers DMs and mentions.",
    guide_url="https://developers.mattermost.com/integrate/admin-guide/admin-bot-accounts/",
    fields=(
        Field(
            "server_url",
            "Server URL",
            secret=False,
            help="The base URL of your Mattermost installation.",
            placeholder="https://mattermost.example.com",
        ),
        Field(
            "bot_token",
            "Bot access token",
            secret=True,
            help="A bot personal access token created in your Mattermost server.",
        ),
    ),
    verified_live=False,
)


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


def _headers(bot_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bot_token}"}


def _ws_url(server_url: str) -> str:
    """Convert an HTTP(S) server URL to its WebSocket counterpart."""
    parsed = urlsplit(server_url.rstrip("/"))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc or parsed.path
    path = parsed.path if parsed.netloc else ""
    return f"{scheme}://{netloc}{path}/api/v4/websocket"


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0))


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """Test connection button: verify the bot token against /api/v4/users/me."""
    server_url = credentials.get("server_url", "").rstrip("/")
    bot_token = credentials.get("bot_token", "")
    if not server_url or not bot_token:
        raise ChannelRefused("server_url and bot_token are required")

    try:
        response = await client.get(
            f"{server_url}/api/v4/users/me",
            headers=_headers(bot_token),
        )
    except Exception as exc:
        raise ChannelRefused(f"Could not reach Mattermost server: {exc}") from exc

    if response.status_code in (401, 403):
        raise ChannelRefused("Mattermost rejected the bot access token")
    if response.status_code >= 400:
        raise ChannelRefused(f"Mattermost returned status {response.status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        raise ChannelRefused("Mattermost did not return a valid JSON object") from exc

    username = data.get("username")
    if not username:
        raise ChannelRefused("Mattermost user object contains no username")
    return f"@{username}"


def split_text(text: str, limit: int = MESSAGE_MAX) -> list[str]:
    return generic.split_on_words(text, limit)


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """Send one message post to Mattermost, keeping thread root if provided in target."""
    server_url = credentials.get("server_url", "").rstrip("/")
    bot_token = credentials.get("bot_token", "")
    if not server_url or not bot_token:
        raise ChannelRefused("Missing server_url or bot_token for Mattermost send")

    if ":" in target:
        channel_id, root_id = target.split(":", 1)
    else:
        channel_id, root_id = target, ""

    payload: dict[str, Any] = {
        "channel_id": channel_id,
        "message": text,
    }
    if root_id:
        payload["root_id"] = root_id

    try:
        response = await client.post(
            f"{server_url}/api/v4/posts",
            headers=_headers(bot_token),
            json=payload,
        )
    except Exception as exc:
        raise ChannelRefused(f"Failed to post message to Mattermost: {exc}") from exc

    if response.status_code >= 400:
        raise ChannelRefused(
            f"Mattermost refused post ({response.status_code}): {response.text[:200]}"
        )


def message_text(event: Any, identity: Any) -> str | None:
    """The answering policy: return clean prompt text or None to ignore the event."""
    if not isinstance(event, dict):
        return None
    if event.get("event") != "posted":
        return None

    data = event.get("data") or {}
    raw_post = data.get("post")
    if isinstance(raw_post, str):
        try:
            post = json.loads(raw_post)
        except ValueError:
            return None
    elif isinstance(raw_post, dict):
        post = raw_post
    else:
        return None

    # Check if author is a bot or self
    bot_id = ""
    bot_username = ""
    if isinstance(identity, dict):
        bot_id = str(identity.get("id") or "")
        bot_username = str(identity.get("username") or "").lstrip("@")
    elif isinstance(identity, str):
        bot_username = identity.lstrip("@")

    user_id = str(post.get("user_id") or "")
    if bot_id and user_id == bot_id:
        return None

    props = post.get("props") or {}
    if str(props.get("from_bot", "")).lower() == "true":
        return None
    if str(props.get("from_webhook", "")).lower() == "true":
        return None

    post_type = str(post.get("type") or "")
    if post_type.startswith("system_"):
        return None

    raw_text = str(post.get("message") or "").strip()
    if not raw_text:
        return None

    channel_type = str(data.get("channel_type") or "")
    if channel_type == "D":
        # Direct messages are always answered
        return raw_text

    if channel_type in ("O", "P", "G"):
        # Public or private channels require @mention
        if not bot_username:
            return None
        mention = f"@{bot_username}"
        if mention.lower() not in raw_text.lower():
            return None
        # Strip the mention
        pattern = re.compile(rf"{re.escape(mention)}\b", re.IGNORECASE)
        cleaned = pattern.sub("", raw_text).strip()
        return cleaned or None

    return None


def reply_target(conversation: Conversation) -> str | None:
    """Where an answer on this thread goes: channel_id:root_id."""
    mm_state = (conversation.state_json or {}).get("mattermost") or {}
    channel_id = mm_state.get("channel_id")
    root_id = mm_state.get("root_id") or ""
    if channel_id:
        return f"{channel_id}:{root_id}" if root_id else channel_id
    return conversation.external_id


async def _find_or_create_conversation(
    db: DbSession,
    channel: Channel,
    external_id: str,
    channel_id: str,
    root_id: str,
) -> tuple[Conversation, bool]:
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == external_id,
            Conversation.status == "open",
        )
    )
    if row is not None:
        state = dict(row.state_json or {})
        mm_state = dict(state.get("mattermost") or {})
        mm_state["channel_id"] = channel_id
        mm_state["root_id"] = root_id
        state["mattermost"] = mm_state
        row.state_json = state
        return row, False

    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=external_id,
        handling="ai",
        status="open",
        state_json={"mattermost": {"channel_id": channel_id, "root_id": root_id}},
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row, True


async def ingest(
    db: DbSession, channel: Channel, event: Any, bot_identity: Any = None
) -> int | None:
    """Store one inbound line. None when duplicate, ignored, or taken over."""
    text = message_text(event, bot_identity)
    if text is None:
        return None

    data = event.get("data") or {}
    raw_post = data.get("post")
    post = json.loads(raw_post) if isinstance(raw_post, str) else raw_post or {}

    post_id = str(post.get("id") or "")
    user_id = str(post.get("user_id") or "")
    channel_id = str(post.get("channel_id") or "")
    root_id = str(post.get("root_id") or "")
    channel_type = str(data.get("channel_type") or "")

    # For threads, keep root_id (or post_id if this is the start of a thread)
    thread_root = root_id or post_id

    # For DMs, conversation identity is the user; for channels, it is the thread
    external_id = user_id if channel_type == "D" else f"{channel_id}:{thread_root}"

    conversation, is_new = await _find_or_create_conversation(
        db, channel, external_id, channel_id, thread_root
    )

    if generic.seen_before(conversation, KIND, post_id):
        return None

    line = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        ts_ms=position_ms(conversation.started_at),
        speaker="caller",
        text=text,
        language=None,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)

    await generic.announce(db, channel, conversation, line, started=is_new)

    if conversation.handling == "human":
        logger.info(
            "mattermost reply withheld, human operator has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None

    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """The shared answer path, told which transport it is answering through."""
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The WebSocket gateway ----------------------------------------------------


async def _run_gateway(
    sessionmaker: async_sessionmaker, channel_id: int, credentials: dict[str, str]
) -> None:
    server_url = credentials.get("server_url", "").rstrip("/")
    bot_token = credentials.get("bot_token", "")
    if not server_url or not bot_token:
        return

    async with make_client() as rest:
        try:
            resp = await rest.get(f"{server_url}/api/v4/users/me", headers=_headers(bot_token))
        except Exception as exc:
            logger.warning(
                "mattermost gateway could not reach server",
                extra={"channel_id": channel_id, "error": str(exc)},
            )
            return
        if resp.status_code >= 400:
            logger.warning(
                "mattermost gateway rejected bot credentials",
                extra={"channel_id": channel_id, "status": resp.status_code},
            )
            return
        bot_user = resp.json()

    ws_url = _ws_url(server_url)
    async with websockets.connect(ws_url, max_size=2**22) as connection:
        await connection.send(
            json.dumps(
                {
                    "seq": 1,
                    "action": "authentication_challenge",
                    "data": {"token": bot_token},
                }
            )
        )

        while True:
            raw = await connection.recv()
            try:
                frame = json.loads(raw)
            except ValueError:
                continue

            if frame.get("event") == "posted":
                async with session_scope(sessionmaker) as db:
                    channel = await db.scalar(
                        select(Channel).where(
                            Channel.id == channel_id, Channel.status == "active"
                        )
                    )
                    if channel is None:
                        return
                    needs_reply = await ingest(db, channel, frame, bot_user)
                if needs_reply is not None:
                    schedule_reply(sessionmaker, channel_id, needs_reply)


async def _connection(
    sessionmaker: async_sessionmaker, channel_id: int, credentials: dict[str, str]
) -> None:
    backoff = 5.0
    while True:
        try:
            await _run_gateway(sessionmaker, channel_id, credentials)
            backoff = 5.0
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "mattermost gateway dropped",
                extra={"channel_id": channel_id, "error": str(error)[:200]},
            )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300.0)


async def loop(sessionmaker: async_sessionmaker) -> None:
    """The supervisor: one WebSocket connection per active Mattermost channel."""
    running: dict[int, tuple[dict[str, str], asyncio.Task]] = {}
    try:
        while True:
            try:
                async with session_scope(sessionmaker) as db:
                    from api.channels import health

                    rows = await health.usable_channels(db, (KIND,))
                    wanted = {
                        row.id: generic.credentials_of(row)
                        for row in rows
                        if row.status == "active"
                    }

                for channel_id, (creds, task) in list(running.items()):
                    if channel_id not in wanted or wanted[channel_id] != creds or task.done():
                        task.cancel()
                        running.pop(channel_id)
                for channel_id, creds in wanted.items():
                    if channel_id not in running:
                        running[channel_id] = (
                            creds,
                            asyncio.create_task(_connection(sessionmaker, channel_id, creds)),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("mattermost supervisor iteration failed")
            await asyncio.sleep(SUPERVISE_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
