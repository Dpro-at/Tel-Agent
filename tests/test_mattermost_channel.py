"""The Mattermost channel — a bot on the WebSocket gateway, and its settings card.

The gateway loop is thin and not driven directly here; what these tests own is
everything the loop hands work to: `message_text` (the answering policy: DMs always,
public and private channels only when mentioned, and bots never), `ingest` (storage,
dedup, takeover silence), `respond` (delivery before storage against a mock
Mattermost REST API), thread root preservation, and the generic card's contract.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import generic
from api.channels import mattermost as transport
from api.channels.generic import ChannelRefused
from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
BOT_TOKEN = "mattermost-bot-token"  # noqa: S105
SERVER_URL = "https://mattermost.test"
BOT_USER_ID = "bot990001"
BOT_USERNAME = "wagner-bot"
CUSTOMER_USER_ID = "cust2440001"


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


async def _drain() -> None:
    """Let scheduled replies finish so nothing runs mid-flight across test boundaries."""
    pending = list(generic._REPLIES)
    if pending:
        await asyncio.gather(*pending)


class FakeMattermost:
    """Mock of Mattermost REST API endpoints."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.headers: list[dict[str, str]] = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            return httpx.Response(
                401, json={"message": "401: Unauthorized", "status_code": 401}
            )
        self.headers.append(dict(request.headers))
        if request.url.path.endswith("/api/v4/users/me"):
            return httpx.Response(200, json={"id": BOT_USER_ID, "username": BOT_USERNAME})
        if request.method == "POST" and request.url.path.endswith("/api/v4/posts"):
            body = json.loads(request.content)
            self.sent.append((request.url.path, body))
            return httpx.Response(201, json={"id": f"post-{len(self.sent)}", **body})
        return httpx.Response(404, json={"message": "unknown", "status_code": 404})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=SERVER_URL, transport=httpx.MockTransport(self.handler)
        )


def _dm(
    text: str = "Do you open on Saturday?",
    *,
    user: str = CUSTOMER_USER_ID,
    post_id: str = "p1",
    root_id: str = "",
    channel_id: str = "dm-room-1",
    from_bot: bool = False,
    from_webhook: bool = False,
    post_type: str = "",
) -> dict[str, Any]:
    post_data: dict[str, Any] = {
        "id": post_id,
        "channel_id": channel_id,
        "user_id": user,
        "message": text,
        "root_id": root_id,
        "type": post_type,
        "props": {},
    }
    if from_bot:
        post_data["props"]["from_bot"] = "true"
    if from_webhook:
        post_data["props"]["from_webhook"] = "true"

    return {
        "event": "posted",
        "data": {
            "channel_type": "D",
            "channel_id": channel_id,
            "post": json.dumps(post_data),
        },
    }


def _channel_post(
    text: str,
    *,
    mentions_bot: bool = False,
    channel_type: str = "O",
    channel_id: str = "channel-town-square",
    post_id: str = "p2",
    root_id: str = "",
    user: str = CUSTOMER_USER_ID,
    from_bot: bool = False,
) -> dict[str, Any]:
    body_text = f"@{BOT_USERNAME} {text}" if mentions_bot else text
    post_data: dict[str, Any] = {
        "id": post_id,
        "channel_id": channel_id,
        "user_id": user,
        "message": body_text,
        "root_id": root_id,
        "type": "",
        "props": {},
    }
    if from_bot:
        post_data["props"]["from_bot"] = "true"

    return {
        "event": "posted",
        "data": {
            "channel_type": channel_type,
            "channel_id": channel_id,
            "post": json.dumps(post_data),
        },
    }


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="mattermost",
        name="Mattermost",
        credentials_encrypted=json.dumps({"bot_token": BOT_TOKEN}),
        settings_json={"fields": {"server_url": SERVER_URL}},
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
    fake = FakeMattermost()
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
            await _drain()
            for http in clients.values():
                await http.aclose()


async def _channel_row(db: AsyncSession, channel_id: int) -> Channel:
    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.id == channel_id))
    assert row is not None
    return row


# --- Unit tests: URLs, text splitting, probe, send ----------------------------


def test_ws_url_derivation() -> None:
    assert (
        transport._ws_url("https://chat.example.com")
        == "wss://chat.example.com/api/v4/websocket"
    )
    assert (
        transport._ws_url("http://chat.example.com:8065")
        == "ws://chat.example.com:8065/api/v4/websocket"
    )
    assert (
        transport._ws_url("https://chat.example.com/subpath/")
        == "wss://chat.example.com/subpath/api/v4/websocket"
    )


def test_split_text_respects_limit() -> None:
    short = "Hello, how can I help you today?"
    assert transport.split_text(short, limit=100) == [short]

    long_text = "word " * 50  # 250 chars
    pieces = transport.split_text(long_text.strip(), limit=50)
    assert len(pieces) > 1
    for piece in pieces:
        assert len(piece) <= 50
    assert " ".join(pieces) == long_text.strip()


async def test_probe_verifies_username() -> None:
    fake = FakeMattermost()
    async with fake.client() as client:
        identity = await transport.probe(
            client, {"server_url": SERVER_URL, "bot_token": BOT_TOKEN}
        )
        assert identity == f"@{BOT_USERNAME}"


async def test_probe_fails_on_missing_fields_or_rejected_token() -> None:
    fake = FakeMattermost()
    async with fake.client() as client:
        with pytest.raises(ChannelRefused, match="server_url and bot_token are required"):
            await transport.probe(client, {"server_url": SERVER_URL})

        fake.refuse = True
        with pytest.raises(ChannelRefused, match="Mattermost rejected the bot access token"):
            await transport.probe(client, {"server_url": SERVER_URL, "bot_token": "bad-token"})


# --- The answering policy -----------------------------------------------------


def test_a_dm_always_answers_and_a_bot_never_does() -> None:
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}
    assert transport.message_text(_dm(), bot_identity) == "Do you open on Saturday?"

    # Author is a bot
    assert transport.message_text(_dm(from_bot=True), bot_identity) is None

    # Post from webhook
    assert transport.message_text(_dm(from_webhook=True), bot_identity) is None

    # System post
    assert transport.message_text(_dm(post_type="system_header_change"), bot_identity) is None

    # Post by the bot itself
    assert transport.message_text(_dm(user=BOT_USER_ID), bot_identity) is None


def test_channel_talk_answers_only_when_mentioned_and_mention_is_stripped() -> None:
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    # Public channel without mention -> ignored
    ignored = _channel_post("anyone around?", mentions_bot=False, channel_type="O")
    assert transport.message_text(ignored, bot_identity) is None

    # Public channel with mention -> answered with mention stripped
    asked = _channel_post("do you open on Saturday?", mentions_bot=True, channel_type="O")
    assert transport.message_text(asked, bot_identity) == "do you open on Saturday?"

    # Private channel with mention -> answered
    private_asked = _channel_post(
        "is this private room monitored?", mentions_bot=True, channel_type="P"
    )
    assert (
        transport.message_text(private_asked, bot_identity) == "is this private room monitored?"
    )

    # String identity accepted as well
    assert transport.message_text(asked, f"@{BOT_USERNAME}") == "do you open on Saturday?"


def test_irrelevant_or_malformed_events_are_ignored() -> None:
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}
    assert transport.message_text({}, bot_identity) is None
    assert transport.message_text({"event": "typing"}, bot_identity) is None
    assert transport.message_text({"event": "posted", "data": {}}, bot_identity) is None
    assert (
        transport.message_text({"event": "posted", "data": {"post": "not-json"}}, bot_identity)
        is None
    )


# --- Ingest, respond, and thread grouping -------------------------------------


async def test_a_dm_becomes_a_conversation_and_the_agent_answers_into_the_room(
    stage,
) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    needs_reply = await transport.ingest(db, channel, _dm(), bot_identity)
    assert needs_reply is not None

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    assert len(fake.sent) == 1
    path, body = fake.sent[0]
    assert path.endswith("/api/v4/posts")
    assert body["channel_id"] == "dm-room-1"
    assert body["message"] == GREETING
    # DMs use post_id as root_id for the thread
    assert body["root_id"] == "p1"

    db.expire_all()
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CUSTOMER_USER_ID)
    )
    assert thread is not None
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == thread.id)))
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller", "agent"]


async def test_channel_mention_keeps_incoming_root_id(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    event = _channel_post(
        "schedule an appointment",
        mentions_bot=True,
        channel_id="c-team-1",
        post_id="p-child-42",
        root_id="p-root-10",
    )
    needs_reply = await transport.ingest(db, channel, event, bot_identity)
    assert needs_reply is not None

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    assert len(fake.sent) == 1
    _, body = fake.sent[0]
    assert body["channel_id"] == "c-team-1"
    assert body["root_id"] == "p-root-10"


async def test_a_repeated_event_is_dropped_by_post_id(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    assert await transport.ingest(db, channel, _dm(post_id="dup-1"), bot_identity) is not None
    assert await transport.ingest(db, channel, _dm(post_id="dup-1"), bot_identity) is None

    db.expire_all()
    assert len((await db.execute(select(Message))).scalars().all()) == 1


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    needs_reply = await transport.ingest(db, channel, _dm(), bot_identity)
    assert needs_reply is not None

    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CUSTOMER_USER_ID)
    )
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)
    assert fake.sent == []

    # Subsequent lines are not queued for reply either
    follow_up = _dm("Are you still there?", post_id="p-followup")
    assert await transport.ingest(db, channel, follow_up, bot_identity) is None


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    needs_reply = await transport.ingest(db, channel, _dm(), bot_identity)
    assert needs_reply is not None

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    db.expire_all()
    rows = (await db.execute(select(Message))).scalars().all()
    assert [row.speaker for row in rows] == ["caller"]


# --- The settings card --------------------------------------------------------


async def test_the_card_declares_fields_and_masks_token(stage) -> None:
    clients, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/mattermost")).json()

    assert body["setup"]["title"] == "Mattermost"
    assert [field["name"] for field in body["setup"]["fields"]] == [
        "server_url",
        "bot_token",
    ]
    assert [field["secret"] for field in body["setup"]["fields"]] == [False, True]
    assert [field["required"] for field in body["setup"]["fields"]] == [True, True]
    assert body["values"]["server_url"] == SERVER_URL
    assert body["previews"]["bot_token"].endswith("oken")
    assert body["webhook_url"] is None  # Mattermost is dial-out, no public door


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/mattermost",
        json={"fields": {"bot_token": "fresh-mattermost-token-9988"}},
    )
    assert saved.status_code == 200, saved.text
    assert "fresh-mattermost-token-9988" not in json.dumps(saved.json())
    assert saved.json()["previews"]["bot_token"].endswith("9988")
    assert saved.json()["values"]["server_url"] == SERVER_URL


async def test_removing_the_token_switches_the_channel_off_with_it(stage) -> None:
    clients, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/mattermost", json={"fields": {"bot_token": ""}}
    )
    assert cleared.json()["previews"]["bot_token"] is None
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    refused = await clients["mohamed"].put("/api/channels/mattermost", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/mattermost")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/mattermost", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/mattermost/test")).status_code == 403


async def test_the_connection_test_verifies_bot_and_stores_identity(stage) -> None:
    clients, _, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/mattermost/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": f"@{BOT_USERNAME}"}

    card = (await clients["mohamed"].get("/api/channels/mattermost")).json()
    assert card["identity"] == f"@{BOT_USERNAME}"


async def test_a_bad_token_fails_the_test_without_confirming_anything_else(stage) -> None:
    clients, _, fake, _, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/mattermost/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "mattermost_refused"


# --- The takeover reply, delivered --------------------------------------------


async def test_a_human_reply_reaches_the_room_and_the_record_in_that_order(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    await transport.ingest(
        db,
        channel,
        _dm("I need to speak with a human agent.", post_id="p-cust-1"),
        bot_identity,
    )
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CUSTOMER_USER_ID)
    )
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply",
        json={"text": "Hello, this is Sabine from customer care."},
    )
    assert sent.status_code == 201, sent.text
    assert len(fake.sent) == 1
    path, body = fake.sent[-1]
    assert path.endswith("/api/v4/posts")
    assert body["channel_id"] == "dm-room-1"
    assert body["message"] == "Hello, this is Sabine from customer care."
    assert body["root_id"] == "p-cust-1"


async def test_an_undelivered_reply_is_not_written_into_the_record(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    bot_identity = {"id": BOT_USER_ID, "username": BOT_USERNAME}

    await transport.ingest(db, channel, _dm(post_id="p-cust-2"), bot_identity)
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CUSTOMER_USER_ID)
    )
    assert thread is not None
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
    assert last is not None and before is not None
    assert last.id == before.id
