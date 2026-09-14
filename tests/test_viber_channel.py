"""The Viber channel — signed callbacks through the public door, and its card.

The fake platform is the REST bot API's account-info, set-webhook and send endpoints on
`httpx.MockTransport`. Callbacks are real HTTP requests to the real door, signed the way
Viber signs them, so the signature check under test is the one production runs. The
fake's `set_webhook` calls the door back before it answers, as Viber does, which is
what proves the switch is committed before the platform is told.
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
from api.channels import viber as transport
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
AUTH_TOKEN = "445da6az1s345z78-dazcczb2542zv51a-viber4711"  # noqa: S105
# Thirty characters: two more than Viber takes.
SENDER = "Wagner & Partner Kundenservice"
CUSTOMER = "01234567890A="
PUBLIC_BASE = "https://agent.example.com"
WEBHOOK_PATH = "viber-door-path-for-tests"
DOOR = f"/public/viber/{WEBHOOK_PATH}"


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


def _signed(body: bytes, token: str = AUTH_TOKEN) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        transport.SIGNATURE_HEADER: transport.signature_for(token, body),
    }


class FakeViber:
    """The REST bot API endpoints the transport uses.

    `door` is the real application, set once it is running, so `set_webhook` can check
    the address the way Viber does before it answers.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.webhooks: list[dict[str, Any]] = []
        self.webhook_checks: list[int] = []
        self.refuse = False
        self.door: AsyncClient | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get(transport.TOKEN_HEADER) != AUTH_TOKEN:
            return httpx.Response(200, json={"status": 2, "status_message": "invalidAuthToken"})
        if self.refuse:
            # Viber refuses with a successful HTTP response and a non-zero status.
            return httpx.Response(
                200, json={"status": 6, "status_message": "receiverNotSubscribed"}
            )
        body = json.loads(request.content) if request.content else {}
        if request.url.path == "/pa/get_account_info":
            return httpx.Response(
                200,
                json={"status": 0, "status_message": "ok", "name": "Wagner", "uri": "wagner"},
            )
        if request.url.path == "/pa/set_webhook":
            self.webhooks.append(body)
            assert self.door is not None
            check = json.dumps(
                {"event": "webhook", "timestamp": 1_700_000_000_000, "message_token": 1}
            ).encode()
            path = str(body["url"]).removeprefix(PUBLIC_BASE)
            answer = await self.door.post(path, content=check, headers=_signed(check))
            self.webhook_checks.append(answer.status_code)
            if answer.status_code != 200:
                return httpx.Response(200, json={"status": 1, "status_message": "invalidUrl"})
            return httpx.Response(
                200, json={"status": 0, "status_message": "ok", "event_types": ["message"]}
            )
        if request.url.path == "/pa/send_message":
            self.sent.append(body)
            return httpx.Response(
                200, json={"status": 0, "status_message": "ok", "message_token": 99}
            )
        return httpx.Response(404, json={"status": 3, "status_message": "badData"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=transport.API_BASE, transport=httpx.MockTransport(self.handler)
        )


def _event(
    text: str = "Do you open on Saturday?",
    *,
    token: int = 4912661846655238145,
    kind: str = "text",
    event: str = "message",
) -> dict[str, Any]:
    return {
        "event": event,
        "timestamp": 1_700_000_000_000,
        "chat_hostname": "SN-CHAT-01_",
        "message_token": token,
        "sender": {
            "id": CUSTOMER,
            "name": "Sabine",
            "language": "de",
            "country": "AT",
            "api_version": 10,
        },
        "message": {"type": kind, "text": text},
        "silent": False,
    }


async def _deliver(
    public: AsyncClient, event: dict[str, Any], signature: str | None = None
) -> httpx.Response:
    body = json.dumps(event).encode()
    headers = _signed(body)
    if signature is not None:
        headers[transport.SIGNATURE_HEADER] = signature
    return await public.post(DOOR, content=body, headers=headers)


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="viber",
        name="Viber",
        credentials_encrypted=json.dumps({"auth_token": AUTH_TOKEN}),
        settings_json={"fields": {"sender_name": SENDER}},
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
    await installs.install(migrated, mine.id, "viber")
    await migrated.commit()

    ids = {"channel": channel.id, "workspace": mine.id}
    fake = FakeViber()
    monkeypatch.setattr(transport, "make_client", fake.client)

    app = create_app(
        settings.model_copy(
            update={"database_url": database_url, "public_base_url": PUBLIC_BASE}
        )
    )
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
        fake.door = public
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


# --- The answering policy -------------------------------------------------------------


def test_a_text_message_from_a_person_is_answered_and_nothing_else_is() -> None:
    """A bot account has no group chats and bots do not message it, so what is left to
    tell apart is a person's text from every other callback."""
    assert transport.message_text(_event(), None) == "Do you open on Saturday?"
    assert transport.message_text(_event(kind="sticker"), None) is None
    assert transport.message_text(_event(kind="picture", text=""), None) is None
    for other in ("webhook", "subscribed", "conversation_started", "delivered", "seen"):
        assert transport.message_text(_event(event=other), None) is None
    nameless = _event()
    nameless["sender"] = {"name": "no id"}
    assert transport.message_text(nameless, None) is None


def test_the_signature_is_over_the_exact_bytes() -> None:
    body = json.dumps(_event()).encode()
    good = transport.signature_for(AUTH_TOKEN, body)
    assert transport.verify_signature(AUTH_TOKEN, body, good)
    assert transport.verify_signature(AUTH_TOKEN, body, good.upper())
    assert not transport.verify_signature(AUTH_TOKEN, body + b" ", good)
    assert not transport.verify_signature(AUTH_TOKEN, body, "مرحبا")
    assert not transport.verify_signature("", body, good)


# --- The door -------------------------------------------------------------------------


async def test_a_signed_message_is_acknowledged_stored_and_answered(stage) -> None:
    _, public, _, fake, db, _ = stage

    answer = await _deliver(public, _event())

    assert answer.status_code == 200, answer.text
    await _drain()
    assert fake.sent == [
        {
            "receiver": CUSTOMER,
            "type": "text",
            "sender": {"name": SENDER[: transport.SENDER_NAME_MAX]},
            "text": GREETING,
        }
    ]
    assert await _lines(db) == [("caller", "Do you open on Saturday?"), ("agent", GREETING)]
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None


async def test_receipts_and_subscriptions_are_acknowledged_and_not_answered(stage) -> None:
    _, public, _, fake, db, _ = stage
    for other in ("delivered", "seen", "subscribed", "conversation_started"):
        answer = await _deliver(public, _event(event=other))
        assert answer.status_code == 200, answer.text
    await _drain()
    assert fake.sent == []
    assert await _lines(db) == []


async def test_switching_on_registers_the_webhook_and_viber_checks_the_door(stage) -> None:
    """Viber calls the door before `set_webhook` answers. With the switch still
    uncommitted the door would refuse, and Viber would refuse the address."""
    clients, _, ids, fake, db, _ = stage
    off = await clients["mohamed"].put("/api/channels/viber", json={"enabled": False})
    assert off.status_code == 200, off.text
    assert fake.webhooks == []

    on = await clients["mohamed"].put("/api/channels/viber", json={"enabled": True})

    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True
    assert fake.webhook_checks == [200]
    assert fake.webhooks == [
        {
            "url": on.json()["webhook_url"],
            "event_types": ["message"],
            "send_name": True,
            "send_photo": False,
        }
    ]
    assert on.json()["webhook_url"] == f"{PUBLIC_BASE}{DOOR}"
    assert (await _channel_row(db, ids["channel"])).status == "active"


async def test_without_a_public_https_address_the_channel_cannot_be_switched_on(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients, _, ids, fake, db, app = stage
    await clients["mohamed"].put("/api/channels/viber", json={"enabled": False})
    monkeypatch.setattr(
        app.state, "settings", app.state.settings.model_copy(update={"public_base_url": None})
    )

    refused = await clients["mohamed"].put("/api/channels/viber", json={"enabled": True})

    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "viber_refused"
    assert fake.webhooks == []
    assert (await _channel_row(db, ids["channel"])).status == "disabled"


async def test_every_reason_the_door_says_no_reads_exactly_alike(stage) -> None:
    clients, public, _, _, _, _ = stage
    event = _event()
    body = json.dumps(event).encode()
    refusals = [
        await _deliver(public, event, signature="not-the-signature"),
        await _deliver(public, event, signature=""),
        await _deliver(public, event, signature=transport.signature_for("wrong-token", body)),
        await public.post("/public/viber/not-an-address", content=body, headers=_signed(body)),
        await public.post(DOOR, content=b"[]", headers=_signed(b"[]")),
    ]
    await clients["mohamed"].put("/api/channels/viber", json={"enabled": False})
    refusals.append(await _deliver(public, event))

    for refused in refusals:
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_recognised"


async def test_a_redelivered_event_is_dropped_by_its_message_token(stage) -> None:
    _, public, _, _, db, _ = stage
    await _deliver(public, _event(token=7))
    await _drain()
    await _deliver(public, _event(token=7))
    await _drain()

    assert [speaker for speaker, _ in await _lines(db)] == ["caller", "agent"]


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, public, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    first = await transport.ingest(db, channel, _event("hello", token=1))
    assert first is not None
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    await _deliver(public, _event("still there?", token=2))
    await _drain()

    assert fake.sent == []
    assert await _lines(db) == [("caller", "hello"), ("caller", "still there?")]


async def test_a_pass_rule_hands_the_person_to_a_human(stage) -> None:
    _, public, ids, fake, db, _ = stage
    db.add(Rule(workspace_id=ids["workspace"], pattern=CUSTOMER, action="pass", note="VIP"))
    await db.commit()

    await _deliver(public, _event("Call me back."))
    await _drain()

    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None and thread.handling == "human"
    tray = (await db.execute(select(Notification))).scalars().all()
    assert [entry.message_key for entry in tray] == ["routed_to_person"]
    assert fake.sent == []


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    """Viber refuses with HTTP 200 and a non-zero status, which must still count."""
    _, public, _, fake, db, _ = stage
    fake.refuse = True

    await _deliver(public, _event("hello"))
    await _drain()

    assert await _lines(db) == [("caller", "hello")]


# --- The settings card ----------------------------------------------------------------


async def test_the_card_declares_the_token_and_sender_and_prints_the_door(stage) -> None:
    clients, _, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/viber")).json()

    assert body["setup"]["title"] == "Viber"
    assert [field["name"] for field in body["setup"]["fields"]] == ["auth_token", "sender_name"]
    assert [field["secret"] for field in body["setup"]["fields"]] == [True, False]
    assert body["setup"]["verified_live"] is False
    assert body["previews"]["auth_token"].endswith("4711")
    assert body["values"]["sender_name"] == SENDER
    assert body["webhook_url"] == f"{PUBLIC_BASE}{DOOR}"


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _, _ = stage
    await clients["mohamed"].put("/api/channels/viber", json={"enabled": False})
    saved = await clients["mohamed"].put(
        "/api/channels/viber", json={"fields": {"auth_token": "fresh-viber-token-9988"}}
    )
    assert saved.status_code == 200, saved.text
    assert "fresh-viber-token-9988" not in json.dumps(saved.json())
    assert saved.json()["previews"]["auth_token"].endswith("9988")


async def test_removing_the_secret_switches_the_channel_off_with_it(stage) -> None:
    clients, _, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/viber", json={"fields": {"auth_token": ""}}
    )
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    refused = await clients["mohamed"].put("/api/channels/viber", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/viber")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/viber", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/viber/test")).status_code == 403


async def test_the_test_button_reports_the_account_and_reports_refusal(stage) -> None:
    clients, _, _, fake, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/viber/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": "Wagner (wagner)"}

    fake.refuse = True
    refused = await clients["mohamed"].post("/api/channels/viber/test")
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "viber_refused"


# --- The takeover reply, delivered -----------------------------------------------------


async def test_a_human_reply_reaches_the_person(stage) -> None:
    clients, _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _event("a person, please"))
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello, Sabine here."}
    )

    assert sent.status_code == 201, sent.text
    assert fake.sent == [
        {
            "receiver": CUSTOMER,
            "type": "text",
            "sender": {"name": SENDER[: transport.SENDER_NAME_MAX]},
            "text": "Hello, Sabine here.",
        }
    ]
