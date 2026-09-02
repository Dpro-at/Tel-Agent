"""The Telegram channel — §B13's first platform transport, and its settings card.

Telegram itself is a stand-in throughout: an `httpx.MockTransport` playing the Bot
API, so the tests exercise this product's half of the conversation — what it stores,
what it sends, what it refuses — without a bot, a network, or Telegram's cooperation.

The two rules that matter most are inherited ones, tested here so they cannot drift:
a taken-over thread gets no generated reply (the widget's silence rule, §A6.7), and a
human reply is delivered *before* it is stored, because on a push transport a stored
line that never reached the customer would be a transcript lying about what the
business said.
"""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import telegram
from api.config import Settings
from api.main import create_app
from api.models import (
    BackgroundJob,
    Channel,
    Conversation,
    Membership,
    Message,
    Notification,
    Rule,
    User,
    Webhook,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
TOKEN = "123456:bot-token-from-botfather"  # noqa: S105


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    """The bot token lives in an encrypted column, so a key is not optional."""
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


class FakeTelegram:
    """The Bot API, reduced to what the transport asks of it.

    `updates` is what the next `getUpdates` hands back; `sent` is every message the
    product delivered. `refuse` makes every call answer `ok: false`, which is what a
    revoked token looks like from this side.
    """

    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.sent: list[dict] = []
        self.actions: list[dict] = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if self.refuse:
            return httpx.Response(200, json={"ok": False, "description": "Unauthorized"})
        payload = json.loads(request.content or b"{}")
        if method == "getMe":
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 1, "username": "wagner_bot"}}
            )
        if method == "getUpdates":
            offset = int(payload.get("offset") or 0)
            due = [u for u in self.updates if u["update_id"] >= offset]
            return httpx.Response(200, json={"ok": True, "result": due})
        if method == "sendMessage":
            self.sent.append(payload)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if method == "sendChatAction":
            self.actions.append(payload)
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": False, "description": "unknown method"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://telegram.test", transport=httpx.MockTransport(self.handler)
        )


def _update(update_id: int, chat_id: int, text: str | None) -> dict:
    message: dict = {"message_id": update_id, "chat": {"id": chat_id}}
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    """One workspace with an active Telegram channel, and the fake platform."""
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="telegram",
        name="Telegram",
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
    fake = FakeTelegram()
    monkeypatch.setattr(telegram, "make_client", fake.client)

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "sabine", "lukas"):
            http = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients, ids, fake, migrated
        finally:
            for http in clients.values():
                await http.aclose()


# --- The transport ------------------------------------------------------------


async def test_a_message_becomes_a_conversation_and_the_agent_answers(stage) -> None:
    _, _, fake, db = stage
    fake.updates = [_update(11, 700, "Do you open on Saturday?")]

    async with fake.client() as client:
        handled = await telegram.poll_once(db, client)
    assert handled == 1

    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "700"))
    assert thread is not None
    rows = (
        (
            await db.execute(
                select(Message).where(Message.conversation_id == thread.id).order_by(Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(r.speaker, r.text) for r in rows] == [
        ("caller", "Do you open on Saturday?"),
        ("agent", GREETING),
    ]
    # And the answer actually left, in one piece, to the right chat.
    assert [(m["chat_id"], m["text"]) for m in fake.sent] == [("700", GREETING)]


async def test_the_offset_advances_so_nothing_is_answered_twice(stage) -> None:
    _, ids, fake, db = stage
    fake.updates = [_update(11, 700, "hello")]

    async with fake.client() as client:
        await telegram.poll_once(db, client)
        # The same update is still in Telegram's hand; a confirmed offset skips it.
        again = await telegram.poll_once(db, client)
    assert again == 0
    assert len(fake.sent) == 1

    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.id == ids["channel"]))
    assert row.settings_json["poll_offset"] == 12


async def test_a_second_message_continues_the_same_thread(stage) -> None:
    _, _, fake, db = stage
    fake.updates = [_update(11, 700, "hello")]
    async with fake.client() as client:
        await telegram.poll_once(db, client)
        fake.updates = [_update(12, 700, "one more thing")]
        await telegram.poll_once(db, client)

    db.expire_all()
    threads = (
        (await db.execute(select(Conversation).where(Conversation.external_id == "700")))
        .scalars()
        .all()
    )
    assert len(threads) == 1


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    """§A6.7's silence rule, holding on the second transport."""
    _, _, fake, db = stage
    fake.updates = [_update(11, 700, "I would rather talk to a person.")]
    async with fake.client() as client:
        await telegram.poll_once(db, client)

    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "700"))
    thread_id = thread.id
    thread.handling = "human"
    await db.commit()
    fake.sent.clear()

    fake.updates = [_update(12, 700, "So is anyone there?")]
    async with fake.client() as client:
        await telegram.poll_once(db, client)

    assert fake.sent == []
    db.expire_all()
    rows = (
        (
            await db.execute(
                select(Message).where(Message.conversation_id == thread_id).order_by(Message.id)
            )
        )
        .scalars()
        .all()
    )
    # The customer's line is in the record; no agent line followed it.
    assert [r.speaker for r in rows][-1] == "caller"


async def test_an_update_without_text_is_skipped_whole(stage) -> None:
    _, _, fake, db = stage
    fake.updates = [_update(11, 700, None)]
    async with fake.client() as client:
        await telegram.poll_once(db, client)

    db.expire_all()
    assert (
        await db.scalar(select(Conversation).where(Conversation.external_id == "700"))
    ) is None
    assert fake.sent == []


async def test_the_registered_hooks_are_told_like_the_web_channels(stage) -> None:
    _, ids, fake, db = stage
    db.add(
        Webhook(
            workspace_id=ids["workspace"],
            url="https://wagner-partner.test/hooks",
            events=["conversation.started", "message.received"],
            secret="a-shared-secret",  # noqa: S106
            enabled=True,
        )
    )
    await db.commit()

    fake.updates = [_update(11, 700, "hallo")]
    async with fake.client() as client:
        await telegram.poll_once(db, client)

    queued = (
        await db.scalars(select(BackgroundJob).where(BackgroundJob.kind == "webhook"))
    ).all()
    assert sorted(job.payload["event"] for job in queued) == [
        "conversation.started",
        "message.received",
    ]
    started = next(j for j in queued if j.payload["event"] == "conversation.started")
    assert started.payload["data"]["channel"] == "telegram"


async def test_a_refused_poll_does_not_kill_the_pass(stage) -> None:
    """A revoked token is one channel's bad day, not the loop's."""
    _, _, fake, db = stage
    fake.refuse = True
    fake.updates = [_update(11, 700, "hello")]
    async with fake.client() as client:
        handled = await telegram.poll_once(db, client)
    assert handled == 0
    assert fake.sent == []


# --- The settings card --------------------------------------------------------


async def test_the_token_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/telegram", json={"bot_token": "999:fresh-token"}
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert "999:fresh-token" not in json.dumps(body)
    assert body["bot_token_preview"].endswith("oken")


async def test_sending_the_mask_back_does_not_overwrite_the_token(stage) -> None:
    clients, ids, _, db = stage
    preview = (await clients["mohamed"].get("/api/channels/telegram")).json()[
        "bot_token_preview"
    ]
    assert preview is not None
    saved = await clients["mohamed"].put("/api/channels/telegram", json={"bot_token": preview})
    assert saved.status_code == 200

    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.id == ids["channel"]))
    assert row.credentials_encrypted == TOKEN


async def test_removing_the_token_switches_the_channel_off_with_it(stage) -> None:
    clients, _, _, _ = stage
    saved = await clients["mohamed"].put("/api/channels/telegram", json={"bot_token": ""})
    assert saved.status_code == 200
    assert saved.json() == {
        "enabled": False,
        "bot_token_preview": None,
        "bot_username": None,
    }

    refused = await clients["mohamed"].put("/api/channels/telegram", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "no_bot_token"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/telegram")).status_code == 200
    refused = await clients["lukas"].put("/api/channels/telegram", json={"enabled": False})
    assert refused.status_code == 403


async def test_the_connection_test_names_the_bot_and_remembers_it(stage) -> None:
    clients, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/telegram/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "bot_username": "wagner_bot"}

    read = await clients["mohamed"].get("/api/channels/telegram")
    assert read.json()["bot_username"] == "wagner_bot"


async def test_a_bad_token_fails_the_test_without_confirming_anything_else(stage) -> None:
    clients, _, fake, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/telegram/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "telegram_refused"


# --- The takeover reply, delivered --------------------------------------------


async def _taken_thread(fake: FakeTelegram, db: AsyncSession) -> Conversation:
    fake.updates = [_update(11, 700, "I would rather talk to a person.")]
    async with fake.client() as client:
        await telegram.poll_once(db, client)
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "700"))
    thread.handling = "human"
    await db.commit()
    fake.sent.clear()
    return thread


async def test_a_human_reply_reaches_telegram_and_the_transcript(stage) -> None:
    clients, _, fake, db = stage
    thread = await _taken_thread(fake, db)

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply",
        json={"text": "Yes — this is Sabine. How can I help?"},
    )
    assert sent.status_code == 201, sent.text
    assert [(m["chat_id"], m["text"]) for m in fake.sent] == [
        ("700", "Yes — this is Sabine. How can I help?")
    ]


async def test_an_undelivered_reply_is_not_written_into_the_record(stage) -> None:
    """Delivery first, storage after — a transcript must not say what nobody heard."""
    clients, _, fake, db = stage
    thread = await _taken_thread(fake, db)
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


# --- Milestone 4: the rules engine, end to end through this transport -------------


async def test_a_blocked_identity_leaves_no_trace(stage) -> None:
    """Like a blocked caller who never rings through: nothing stored, nothing sent,
    and the offset still advances so the message is not retried forever."""
    _, ids, fake, db = stage
    db.add(Rule(workspace_id=ids["workspace"], pattern="700", action="block"))
    await db.commit()
    fake.updates = [_update(11, 700, "It is me again!")]

    async with fake.client() as client:
        handled = await telegram.poll_once(db, client)
    assert handled == 1

    db.expire_all()
    assert await db.scalar(select(Conversation)) is None
    assert await db.scalar(select(Message)) is None
    assert fake.sent == []


async def test_a_pass_identity_gets_a_person_not_the_agent(stage) -> None:
    """§A6.5's first column: straight through. The agent stays silent, the thread is
    marked for a person, and the tray says so once - not once per message."""
    _, ids, fake, db = stage
    db.add(Rule(workspace_id=ids["workspace"], pattern="700", action="pass", note="The boss"))
    await db.commit()
    fake.updates = [_update(11, 700, "Call me back.")]

    async with fake.client() as client:
        assert await telegram.poll_once(db, client) == 1

    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "700"))
    assert thread is not None
    assert thread.handling == "human"
    # The message is in the record; no generated answer follows it.
    rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    assert [(r.speaker, r.text) for r in rows] == [("caller", "Call me back.")]
    assert fake.sent == []

    tray = (await db.execute(select(Notification))).scalars().all()
    assert [t.message_key for t in tray] == ["routed_to_person"]
    assert tray[0].detail == "700"

    # A second message keeps the silence and does not notify again.
    fake.updates = [_update(12, 700, "Hello? Anybody?")]
    async with fake.client() as client:
        assert await telegram.poll_once(db, client) == 1
    db.expire_all()
    tray = (await db.execute(select(Notification))).scalars().all()
    assert [t.message_key for t in tray] == ["routed_to_person"]
    assert fake.sent == []
