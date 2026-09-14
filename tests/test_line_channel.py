"""The LINE channel — signed webhook deliveries through the public door, and its card.

The fake platform is the Messaging API's bot-info, reply and push endpoints on
`httpx.MockTransport`. Deliveries are real HTTP requests to the real door, signed the way
LINE signs them, so the signature check under test is the one production runs.
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
from api.channels import line as transport
from api.config import Settings
from api.extensions import installs
from api.main import create_app
from api.models import (
    Channel,
    Conversation,
    Membership,
    Message,
    Notification,
    Rule,
    User,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
CHANNEL_SECRET = "line-channel-secret-4711"  # noqa: S105
ACCESS_TOKEN = "line-access-token-0815"  # noqa: S105
BOT = "U0bot000000000000000000000000000"
CUSTOMER = "U1customer0000000000000000000000"
GROUP = "C9group00000000000000000000000000"
WEBHOOK_PATH = "line-door-path-for-tests"
DOOR = f"/public/line/{WEBHOOK_PATH}"


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
    pending = list(generic._REPLIES)
    if pending:
        await asyncio.gather(*pending)


class FakeLine:
    """The Messaging API endpoints the transport uses."""

    def __init__(self) -> None:
        self.replies: list[dict[str, Any]] = []
        self.pushes: list[dict[str, Any]] = []
        self.refuse = False
        self.reply_token_spent = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse or request.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return httpx.Response(401, json={"message": "Authentication failed"})
        if request.url.path == "/v2/bot/info":
            return httpx.Response(
                200, json={"displayName": "Wagner & Partner", "basicId": "@123abcd"}
            )
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/v2/bot/message/reply":
            if self.reply_token_spent:
                return httpx.Response(400, json={"message": "Invalid reply token"})
            self.reply_token_spent = True
            self.replies.append(body)
            return httpx.Response(200, json={})
        if request.url.path == "/v2/bot/message/push":
            self.pushes.append(body)
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": "Not found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=transport.API_BASE, transport=httpx.MockTransport(self.handler)
        )


def _event(
    text: str = "Do you open on Saturday?",
    *,
    message_id: str = "m1",
    source: dict[str, str] | None = None,
    mentionees: list[dict[str, Any]] | None = None,
    reply_token: str = "reply-token-1",  # noqa: S107
    kind: str = "text",
) -> dict[str, Any]:
    message: dict[str, Any] = {"type": kind, "id": message_id, "text": text}
    if mentionees is not None:
        message["mention"] = {"mentionees": mentionees}
    return {
        "type": "message",
        "mode": "active",
        "timestamp": 1_700_000_000_000,
        "webhookEventId": f"evt-{message_id}",
        "deliveryContext": {"isRedelivery": False},
        "replyToken": reply_token,
        "source": source or {"type": "user", "userId": CUSTOMER},
        "message": message,
    }


def _body(*events: dict[str, Any]) -> bytes:
    return json.dumps({"destination": BOT, "events": list(events)}).encode()


async def _deliver(
    public: AsyncClient, body: bytes, signature: str | None = None
) -> httpx.Response:
    headers = {"Content-Type": "application/json"}
    headers["x-line-signature"] = (
        transport.signature_for(CHANNEL_SECRET, body) if signature is None else signature
    )
    return await public.post(DOOR, content=body, headers=headers)


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="line",
        name="LINE",
        credentials_encrypted=json.dumps(
            {"channel_secret": CHANNEL_SECRET, "channel_access_token": ACCESS_TOKEN}
        ),
        settings_json={},
        webhook_path=WEBHOOK_PATH,
        status="active",
    )
    migrated.add(channel)

    for username, role in (("mohamed", "admin"), ("sabine", "reception"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role=role))
    # The channel's app is installed, as in every workspace that uses the channel.
    await installs.install(migrated, mine.id, "line")
    await migrated.commit()

    ids = {"channel": channel.id, "workspace": mine.id}
    fake = FakeLine()
    monkeypatch.setattr(transport, "make_client", fake.client)

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        asgi = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "sabine", "lukas"):
            http = AsyncClient(transport=asgi, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        public = AsyncClient(transport=asgi, base_url="http://localhost")
        try:
            yield clients, public, ids, fake, migrated, app
        finally:
            await _drain()
            for http in clients.values():
                await http.aclose()
            await public.aclose()


async def _lines(db: AsyncSession) -> list[tuple[str, str]]:
    db.expire_all()
    rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    return [(row.speaker, row.text) for row in rows]


async def _channel_row(db: AsyncSession, channel_id: int) -> Channel:
    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.id == channel_id))
    assert row is not None
    return row


# --- The answering policy ---------------------------------------------------------


def test_a_direct_message_is_answered_and_the_account_itself_never_is() -> None:
    assert transport.message_text(_event(), BOT) == "Do you open on Saturday?"
    assert transport.message_text(_event(source={"type": "user", "userId": BOT}), BOT) is None
    assert transport.message_text(_event(kind="sticker"), BOT) is None
    assert transport.message_text({**_event(), "type": "follow"}, BOT) is None


def test_a_group_message_is_answered_only_when_the_account_is_mentioned() -> None:
    in_group = {"type": "group", "groupId": GROUP, "userId": CUSTOMER}
    assert transport.message_text(_event("anyone around?", source=in_group), BOT) is None

    others = [{"index": 0, "length": 6, "userId": CUSTOMER, "type": "user"}]
    assert (
        transport.message_text(_event("@Sabine hi", source=in_group, mentionees=others), BOT)
        is None
    )

    asked = _event(
        "@Wagner & Partner do you take cards?",
        source=in_group,
        mentionees=[{"index": 0, "length": 17, "type": "user", "isSelf": True}],
    )
    assert transport.message_text(asked, BOT) == "do you take cards?"


def test_a_mention_after_an_emoji_is_cut_by_utf16_position() -> None:
    """The platform counts positions in UTF-16 code units: an emoji before the mention
    is two of them, and cutting by Python characters would take the wrong span."""
    text = "😀 @Wagner & Partner parking?"
    asked = _event(
        text,
        source={"type": "room", "roomId": "R1", "userId": CUSTOMER},
        mentionees=[{"index": 3, "length": 17, "type": "user", "isSelf": True}],
    )
    assert transport.message_text(asked, BOT) == "😀 parking?"


def test_the_signature_is_over_the_exact_bytes() -> None:
    body = _body(_event())
    good = transport.signature_for(CHANNEL_SECRET, body)
    assert transport.verify_signature(CHANNEL_SECRET, body, good)
    assert not transport.verify_signature(CHANNEL_SECRET, body + b" ", good)
    assert not transport.verify_signature(CHANNEL_SECRET, body, "مرحبا")
    assert not transport.verify_signature("", body, good)


# --- The door ---------------------------------------------------------------------


async def test_a_signed_message_is_acknowledged_stored_and_answered_by_reply(stage) -> None:
    _, public, _, fake, db, _ = stage

    answer = await _deliver(public, _body(_event()))

    assert answer.status_code == 200, answer.text
    await _drain()
    assert fake.replies == [
        {"replyToken": "reply-token-1", "messages": [{"type": "text", "text": GREETING}]}
    ]
    assert fake.pushes == []
    assert await _lines(db) == [("caller", "Do you open on Saturday?"), ("agent", GREETING)]


async def test_the_console_verify_delivery_is_acknowledged(stage) -> None:
    """The Verify button sends a signed delivery with no events."""
    _, public, _, fake, db, _ = stage
    answer = await _deliver(public, json.dumps({"destination": BOT, "events": []}).encode())
    assert answer.status_code == 200
    assert await _lines(db) == []
    assert fake.replies == fake.pushes == []


async def test_a_spent_reply_token_falls_back_to_a_push(stage) -> None:
    _, public, _, fake, db, _ = stage
    fake.reply_token_spent = True

    await _deliver(public, _body(_event()))
    await _drain()

    assert fake.pushes == [{"to": CUSTOMER, "messages": [{"type": "text", "text": GREETING}]}]
    assert (await _lines(db))[-1] == ("agent", GREETING)


async def test_a_group_answer_goes_to_the_group(stage) -> None:
    _, public, _, fake, db, _ = stage
    fake.reply_token_spent = True
    in_group = {"type": "group", "groupId": GROUP, "userId": CUSTOMER}
    await _deliver(
        public,
        _body(
            _event("morning all", message_id="g1", source=in_group),
            _event(
                "@Wagner & Partner parking?",
                message_id="g2",
                source=in_group,
                mentionees=[{"index": 0, "length": 17, "type": "user", "isSelf": True}],
            ),
        ),
    )
    await _drain()

    assert [push["to"] for push in fake.pushes] == [GROUP]
    assert (await _lines(db))[0] == ("caller", "parking?")


async def test_every_reason_the_door_says_no_reads_exactly_alike(stage) -> None:
    clients, public, _, _, _, _ = stage
    body = _body(_event())
    refusals = [
        await _deliver(public, body, signature="not-the-signature"),
        await _deliver(public, body, signature=""),
        await _deliver(public, body, signature=transport.signature_for("wrong-secret", body)),
        await public.post(
            "/public/line/not-an-address",
            content=body,
            headers={"x-line-signature": transport.signature_for(CHANNEL_SECRET, body)},
        ),
    ]
    await clients["mohamed"].put("/api/channels/line", json={"enabled": False})
    refusals.append(await _deliver(public, body))

    for refused in refusals:
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_recognised"


async def test_a_redelivered_event_is_dropped_by_its_message_id(stage) -> None:
    _, public, _, _, db, _ = stage
    await _deliver(public, _body(_event(message_id="dup")))
    await _drain()
    again = _event(message_id="dup", reply_token="reply-token-2")  # noqa: S106
    await _deliver(public, _body(again))
    await _drain()

    assert [speaker for speaker, _ in await _lines(db)] == ["caller", "agent"]


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, public, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    assert await transport.ingest(db, channel, {**_event(message_id="t1"), "destination": BOT})
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    await _deliver(public, _body(_event("still there?", message_id="t2")))
    await _drain()

    assert fake.replies == fake.pushes == []


async def test_a_pass_rule_hands_the_person_to_a_human(stage) -> None:
    _, public, ids, fake, db, _ = stage
    db.add(Rule(workspace_id=ids["workspace"], pattern=CUSTOMER, action="pass", note="VIP"))
    await db.commit()

    await _deliver(public, _body(_event("Call me back.")))
    await _drain()

    db.expire_all()
    tray = (await db.execute(select(Notification))).scalars().all()
    assert [entry.message_key for entry in tray] == ["routed_to_person"]
    assert fake.replies == fake.pushes == []


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, public, _, fake, db, _ = stage
    fake.refuse = True

    await _deliver(public, _body(_event()))
    await _drain()

    assert await _lines(db) == [("caller", "Do you open on Saturday?")]


# --- The settings card --------------------------------------------------------------


async def test_the_card_declares_two_secrets_and_prints_the_door(stage) -> None:
    clients, _, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/line")).json()

    assert body["setup"]["title"] == "LINE"
    assert [field["name"] for field in body["setup"]["fields"]] == [
        "channel_secret",
        "channel_access_token",
    ]
    assert all(field["secret"] for field in body["setup"]["fields"])
    assert body["previews"]["channel_secret"].endswith("4711")
    assert body["webhook_url"].endswith(DOOR)


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/line", json={"fields": {"channel_access_token": "fresh-line-token-9988"}}
    )
    assert saved.status_code == 200, saved.text
    assert "fresh-line-token-9988" not in json.dumps(saved.json())
    assert saved.json()["previews"]["channel_access_token"].endswith("9988")


async def test_removing_the_secret_switches_the_channel_off_with_it(stage) -> None:
    clients, _, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/line", json={"fields": {"channel_secret": ""}}
    )
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/line")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/line", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/line/test")).status_code == 403


async def test_the_test_button_reports_the_account_and_reports_refusal(stage) -> None:
    clients, _, _, fake, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/line/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": "Wagner & Partner (@123abcd)"}

    fake.refuse = True
    refused = await clients["mohamed"].post("/api/channels/line/test")
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "line_refused"


# --- The takeover reply, delivered ---------------------------------------------------


async def test_a_human_reply_is_pushed_once_the_reply_token_is_old(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients, _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, {**_event("a person, please"), "destination": BOT})
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    # The token is stale by the time a person answers.
    monkeypatch.setattr(transport, "REPLY_TOKEN_SECONDS", -1)
    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello, Sabine here."}
    )

    assert sent.status_code == 201, sent.text
    assert fake.replies == []
    assert fake.pushes == [
        {"to": CUSTOMER, "messages": [{"type": "text", "text": "Hello, Sabine here."}]}
    ]
