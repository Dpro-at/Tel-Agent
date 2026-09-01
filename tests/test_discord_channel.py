"""The Discord channel — a bot on the gateway, and its settings card.

The gateway loop is deliberately thin and not driven here; what these tests own is
everything the loop hands work to: `message_text` (the answering policy),
`ingest` (storage, dedup, the takeover silence), `respond` (delivery before
storage, against a mock Graph-of-Discord), and the card's contract. The policy
under test is the one the module docstring states: DMs always answer, server
channels only when mentioned, and **bots are never customers**.
"""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import discord as transport
from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
TOKEN = "discord-bot-token"  # noqa: S105
BOT_USER_ID = "990001"


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


class FakeDiscord:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            return httpx.Response(401, json={"message": "401: Unauthorized"})
        if request.method == "POST" and "/messages" in request.url.path:
            self.sent.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"id": "1"})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": BOT_USER_ID, "username": "wagner-bot"})
        return httpx.Response(404, json={"message": "unknown"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://discord.test", transport=httpx.MockTransport(self.handler)
        )


def _dm(
    text: str = "Do you open on Saturday?", *, user: str = "24400001", event_id: str = "e1"
) -> dict:
    return {
        "id": event_id,
        "channel_id": "dm-room-1",
        "author": {"id": user, "bot": False},
        "content": text,
    }


def _guild_message(text: str, *, mentions_bot: bool) -> dict:
    return {
        "id": "e2",
        "channel_id": "guild-room-7",
        "guild_id": "g1",
        "author": {"id": "24400001", "bot": False},
        "content": text,
        "mentions": [{"id": BOT_USER_ID}] if mentions_bot else [],
    }


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="discord",
        name="Discord",
        credentials_encrypted=TOKEN,
        settings_json={},
        status="active",
    )
    migrated.add(channel)

    for username, role in (("mohamed", "admin"), ("sabine", "reception"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role=role))
    await migrated.commit()

    ids = {"channel": channel.id, "workspace": mine.id}
    fake = FakeDiscord()
    monkeypatch.setattr(transport, "make_client", fake.client)

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport_asgi = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "sabine", "lukas"):
            http = AsyncClient(transport=transport_asgi, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients, ids, fake, migrated, app
        finally:
            for http in clients.values():
                await http.aclose()


async def _channel_row(db: AsyncSession, channel_id: int) -> Channel:
    return await db.scalar(select(Channel).where(Channel.id == channel_id))


# --- The answering policy -------------------------------------------------------


def test_a_dm_always_answers_and_a_bot_never_does() -> None:
    assert transport.message_text(_dm(), BOT_USER_ID) == "Do you open on Saturday?"
    robot = _dm()
    robot["author"]["bot"] = True
    assert transport.message_text(robot, BOT_USER_ID) is None


def test_guild_talk_answers_only_when_mentioned_and_the_mention_is_stripped() -> None:
    ignored = _guild_message("anyone around?", mentions_bot=False)
    assert transport.message_text(ignored, BOT_USER_ID) is None

    asked = _guild_message(f"<@{BOT_USER_ID}> do you open on Saturday?", mentions_bot=True)
    assert transport.message_text(asked, BOT_USER_ID) == "do you open on Saturday?"


# --- Ingest and the answer ------------------------------------------------------


async def test_a_dm_becomes_a_conversation_and_the_agent_answers_into_the_room(
    stage,
) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])

    needs_reply = await transport.ingest(db, channel, _dm(), BOT_USER_ID)
    assert needs_reply is not None

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    assert [(path, body["content"]) for path, body in fake.sent] == [
        ("/channels/dm-room-1/messages", GREETING)
    ]
    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "24400001"))
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == thread.id)))
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller", "agent"]


async def test_a_repeated_event_is_dropped_by_discords_own_id(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    assert await transport.ingest(db, channel, _dm(), BOT_USER_ID) is not None
    assert await transport.ingest(db, channel, _dm(), BOT_USER_ID) is None

    db.expire_all()
    assert len((await db.execute(select(Message))).scalars().all()) == 1


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _dm(), BOT_USER_ID)

    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "24400001"))
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)
    assert fake.sent == []

    # And the next message is not even queued for one.
    follow_up = _dm("So is anyone there?", event_id="e9")
    assert await transport.ingest(db, channel, follow_up, BOT_USER_ID) is None


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _dm(), BOT_USER_ID)

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    db.expire_all()
    rows = (await db.execute(select(Message))).scalars().all()
    assert [row.speaker for row in rows] == ["caller"]


# --- The settings card ----------------------------------------------------------


async def test_the_token_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/discord", json={"bot_token": "fresh-token"}
    )
    assert saved.status_code == 200, saved.text
    assert "fresh-token" not in json.dumps(saved.json())
    assert saved.json()["bot_token_preview"].endswith("oken")


async def test_removing_the_token_switches_the_channel_off_with_it(stage) -> None:
    clients, _, _, _, _ = stage
    saved = await clients["mohamed"].put("/api/channels/discord", json={"bot_token": ""})
    assert saved.json()["enabled"] is False
    refused = await clients["mohamed"].put("/api/channels/discord", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "no_bot_token"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/discord")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/discord", json={"enabled": False})
    ).status_code == 403


async def test_the_connection_test_names_the_bot_and_remembers_it(stage) -> None:
    clients, _, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/discord/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "bot_username": "wagner-bot"}
    read = (await clients["mohamed"].get("/api/channels/discord")).json()
    assert read["bot_username"] == "wagner-bot"


async def test_a_bad_token_fails_the_test_without_confirming_anything_else(stage) -> None:
    clients, _, fake, _, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/discord/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "discord_refused"


# --- The takeover reply, delivered ----------------------------------------------


async def test_a_human_reply_reaches_the_room_and_the_record_in_that_order(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _dm("I would rather talk to a person."), BOT_USER_ID)
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "24400001"))
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply",
        json={"text": "Yes — this is Sabine. How can I help?"},
    )
    assert sent.status_code == 201, sent.text
    assert fake.sent[-1][0] == "/channels/dm-room-1/messages"
    assert fake.sent[-1][1]["content"] == "Yes — this is Sabine. How can I help?"


async def test_an_undelivered_reply_is_not_written_into_the_record(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _dm(), BOT_USER_ID)
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "24400001"))
    thread.handling = "human"
    await db.commit()
    before = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))

    fake.refuse = True
    refused = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello?"}
    )
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "not_delivered"

    db.expire_all()
    last = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))
    assert last.id == before.id
