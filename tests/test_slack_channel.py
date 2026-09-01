"""The Slack channel — Socket Mode's conversation half, and its settings card.

The socket loop is thin and not driven here; what these tests own is what it hands
work to: `message_text` (the answering policy — DMs always, channels as mentions
only, nothing with a `bot_id` or a `subtype`), `ingest`, `respond` against a mock
Web API, and the card's pair contract.
"""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import slack as transport
from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
APP_TOKEN = "xapp-1-app-level-token"  # noqa: S105
BOT_TOKEN = "xoxb-bot-token"  # noqa: S105
BOT_USER_ID = "U0BOT"


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


class FakeSlack:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "chat.postMessage":
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "ts": "1.0"})
        if method == "auth.test":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "team": "Wagner & Partner",
                    "user": "telagent",
                    "user_id": BOT_USER_ID,
                },
            )
        if method == "apps.connections.open":
            return httpx.Response(200, json={"ok": True, "url": "wss://slack.test/socket"})
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://slack.test/api", transport=httpx.MockTransport(self.handler)
        )


def _dm(
    text: str = "Do you open on Saturday?", *, user: str = "U2440", ts: str = "100.1"
) -> dict:
    return {
        "type": "message",
        "channel_type": "im",
        "channel": "D0DM",
        "user": user,
        "text": text,
        "ts": ts,
    }


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="slack",
        name="Slack",
        credentials_encrypted=json.dumps({"app_token": APP_TOKEN, "bot_token": BOT_TOKEN}),
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
    fake = FakeSlack()
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


def test_a_dm_always_answers_and_system_lines_never_do() -> None:
    assert transport.message_text(_dm(), BOT_USER_ID) == "Do you open on Saturday?"

    edited = _dm()
    edited["subtype"] = "message_changed"
    assert transport.message_text(edited, BOT_USER_ID) is None

    robot = _dm()
    robot["bot_id"] = "B123"
    assert transport.message_text(robot, BOT_USER_ID) is None

    ourselves = _dm(user=BOT_USER_ID)
    assert transport.message_text(ourselves, BOT_USER_ID) is None


def test_channel_talk_answers_only_as_a_mention_and_the_mention_is_stripped() -> None:
    plain = {
        "type": "message",
        "channel_type": "channel",
        "channel": "C1",
        "user": "U2440",
        "text": "anyone around?",
        "ts": "1.2",
    }
    assert transport.message_text(plain, BOT_USER_ID) is None

    mention = {
        "type": "app_mention",
        "channel": "C1",
        "user": "U2440",
        "text": f"<@{BOT_USER_ID}> do you open on Saturday?",
        "ts": "1.3",
    }
    assert transport.message_text(mention, BOT_USER_ID) == "do you open on Saturday?"


# --- Ingest and the answer ------------------------------------------------------


async def test_a_dm_becomes_a_conversation_and_the_agent_answers_into_the_room(
    stage,
) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])

    needs_reply = await transport.ingest(db, channel, _dm(), BOT_USER_ID, "Ev1")
    assert needs_reply is not None

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    assert [(m["channel"], m["text"]) for m in fake.sent] == [("D0DM", GREETING)]
    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "U2440"))
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == thread.id)))
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller", "agent"]


async def test_a_redelivered_envelope_is_dropped_by_its_event_id(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    assert await transport.ingest(db, channel, _dm(), BOT_USER_ID, "Ev1") is not None
    assert await transport.ingest(db, channel, _dm(), BOT_USER_ID, "Ev1") is None

    db.expire_all()
    assert len((await db.execute(select(Message))).scalars().all()) == 1


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _dm(), BOT_USER_ID, "Ev1")

    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "U2440"))
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)
    assert fake.sent == []

    follow_up = _dm("So is anyone there?", ts="100.9")
    assert await transport.ingest(db, channel, follow_up, BOT_USER_ID, "Ev2") is None


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _dm(), BOT_USER_ID, "Ev1")

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    db.expire_all()
    rows = (await db.execute(select(Message))).scalars().all()
    assert [row.speaker for row in rows] == ["caller"]


# --- The settings card ----------------------------------------------------------


async def test_the_tokens_go_in_together_and_only_masks_come_out(stage) -> None:
    clients, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/slack",
        json={"app_token": "xapp-fresh", "bot_token": "xoxb-fresh"},
    )
    assert saved.status_code == 200, saved.text
    assert "fresh" not in json.dumps(saved.json())
    assert saved.json()["app_token_preview"] is not None

    refused = await clients["mohamed"].put(
        "/api/channels/slack", json={"app_token": "xapp-alone"}
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "credentials_incomplete"


async def test_removing_the_tokens_switches_the_channel_off_with_them(stage) -> None:
    clients, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/slack", json={"app_token": "", "bot_token": ""}
    )
    assert saved.json()["enabled"] is False
    refused = await clients["mohamed"].put("/api/channels/slack", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "credentials_incomplete"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/slack")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/slack", json={"enabled": False})
    ).status_code == 403


async def test_the_connection_test_names_the_workspace_and_the_bot(stage) -> None:
    clients, _, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/slack/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "ok": True,
        "team_name": "Wagner & Partner",
        "bot_name": "telagent",
    }
    read = (await clients["mohamed"].get("/api/channels/slack")).json()
    assert read["team_name"] == "Wagner & Partner"


async def test_refused_tokens_fail_the_test_without_confirming_anything(stage) -> None:
    clients, _, fake, _, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/slack/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "slack_refused"


# --- The takeover reply, delivered ----------------------------------------------


async def test_a_human_reply_reaches_the_room_and_the_record_in_that_order(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(
        db, channel, _dm("I would rather talk to a person."), BOT_USER_ID, "Ev1"
    )
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "U2440"))
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply",
        json={"text": "Yes — this is Sabine. How can I help?"},
    )
    assert sent.status_code == 201, sent.text
    assert fake.sent[-1]["channel"] == "D0DM"
    assert fake.sent[-1]["text"] == "Yes — this is Sabine. How can I help?"


async def test_an_undelivered_reply_is_not_written_into_the_record(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _dm(), BOT_USER_ID, "Ev1")
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "U2440"))
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
