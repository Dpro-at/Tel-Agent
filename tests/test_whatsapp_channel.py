"""The WhatsApp channel — the first platform transport with a webhook, §B13.

Meta is a stand-in throughout: an `httpx.MockTransport` playing the Graph API for
the outbound half, and the tests themselves playing Meta for the inbound half —
signing deliveries with the app secret the way Meta does, so the signature check is
exercised as the guard it is.

The inherited rules under test, on the fourth transport: one conversation per
correspondent, the takeover silence, delivery before storage. The rules this channel
adds: the handshake confirms nothing to a caller without the verify token, a
delivery with a bad signature answers exactly like an unknown address, a retried
delivery is dropped by Meta's own message id — and the answer runs *after* the 200,
on its own session, because Meta retries a slow webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import whatsapp as transport
from api.config import Settings
from api.main import create_app
from api.models import (
    Channel,
    Conversation,
    Membership,
    Message,
    User,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
ACCESS_TOKEN = "EAAG-meta-access-token"  # noqa: S105
APP_SECRET = "meta-app-secret"  # noqa: S105
PHONE_NUMBER_ID = "108500123456"
WEBHOOK_PATH = "wa-" + "a" * 28
VERIFY_TOKEN = "verify-me-please"  # noqa: S105


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    """Meta's credentials live in the encrypted column, so a key is not optional."""
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


class FakeGraph:
    """The Graph API, reduced to what the transport asks of it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            return httpx.Response(401, json={"error": {"message": "Invalid OAuth token"}})
        if request.method == "POST" and request.url.path.endswith("/messages"):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "display_phone_number": "+43 720 111222",
                    "verified_name": "Wagner & Partner",
                },
            )
        return httpx.Response(404, json={"error": {"message": "unknown"}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://graph.test", transport=httpx.MockTransport(self.handler)
        )


def _delivery(
    text: str = "Do you open on Saturday?",
    *,
    wa_id: str = "436601234567",
    wamid: str = "wamid.1",
) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": wa_id,
                                    "id": wamid,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _signed(body: dict) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    signature = hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {
        "X-Hub-Signature-256": f"sha256={signature}",
        "Content-Type": "application/json",
    }


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    """One workspace with an active WhatsApp channel, and the fake platform."""
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="whatsapp",
        name="WhatsApp",
        webhook_path=WEBHOOK_PATH,
        credentials_encrypted=json.dumps(
            {"access_token": ACCESS_TOKEN, "app_secret": APP_SECRET}
        ),
        settings_json={"phone_number_id": PHONE_NUMBER_ID, "verify_token": VERIFY_TOKEN},
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
    fake = FakeGraph()
    monkeypatch.setattr(transport, "make_client", fake.client)

    # The webhook schedules replies as tasks; the tests run them by hand instead,
    # so nothing races the assertions.
    scheduled: list[tuple[int, int]] = []
    monkeypatch.setattr(
        transport,
        "schedule_reply",
        lambda sessionmaker, channel_id, message_id: scheduled.append((channel_id, message_id)),
    )

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        anon = AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://localhost",
        )
        for username in ("mohamed", "sabine", "lukas"):
            http = AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://localhost",
            )
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients, anon, ids, fake, scheduled, migrated, app
        finally:
            await anon.aclose()
            for http in clients.values():
                await http.aclose()


# --- The handshake --------------------------------------------------------------


async def test_the_handshake_echoes_the_challenge_for_the_right_token(stage) -> None:
    _, anon, _, _, _, _, _ = stage
    answer = await anon.get(
        f"/public/whatsapp/{WEBHOOK_PATH}",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "echo-this-back",
        },
    )
    assert answer.status_code == 200
    assert answer.text == "echo-this-back"


async def test_a_wrong_token_and_an_unknown_address_answer_identically(stage) -> None:
    _, anon, _, _, _, _, _ = stage
    wrong = await anon.get(
        f"/public/whatsapp/{WEBHOOK_PATH}",
        params={"hub.mode": "subscribe", "hub.verify_token": "guess", "hub.challenge": "x"},
    )
    unknown = await anon.get(
        "/public/whatsapp/no-such-address",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "x",
        },
    )
    assert wrong.status_code == unknown.status_code == 403
    # Identical apart from the per-request id: the refusal must not say which
    # reason applied.
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


# --- The delivery ---------------------------------------------------------------


async def test_a_signed_delivery_is_stored_and_a_reply_is_scheduled(stage) -> None:
    _, anon, ids, _, scheduled, db, _ = stage
    raw, headers = _signed(_delivery())
    answer = await anon.post(f"/public/whatsapp/{WEBHOOK_PATH}", content=raw, headers=headers)
    assert answer.status_code == 200

    db.expire_all()
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == "+436601234567")
    )
    assert thread is not None
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == thread.id)))
        .scalars()
        .all()
    )
    assert [(line.speaker, line.text) for line in lines] == [
        ("caller", "Do you open on Saturday?")
    ]
    assert scheduled == [(ids["channel"], lines[0].id)]


async def test_a_bad_signature_answers_like_an_unknown_address_and_stores_nothing(
    stage,
) -> None:
    _, anon, _, _, scheduled, db, _ = stage
    raw = json.dumps(_delivery()).encode()
    forged = await anon.post(
        f"/public/whatsapp/{WEBHOOK_PATH}",
        content=raw,
        headers={
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "Content-Type": "application/json",
        },
    )
    unknown = await anon.post("/public/whatsapp/no-such-address", content=raw)
    assert forged.status_code == unknown.status_code == 403
    assert forged.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert forged.json()["error"]["message"] == unknown.json()["error"]["message"]

    db.expire_all()
    assert (await db.execute(select(Message))).scalars().all() == []
    assert scheduled == []


async def test_a_retried_delivery_is_dropped_by_metas_own_message_id(stage) -> None:
    _, anon, _, _, scheduled, db, _ = stage
    raw, headers = _signed(_delivery())
    await anon.post(f"/public/whatsapp/{WEBHOOK_PATH}", content=raw, headers=headers)
    await anon.post(f"/public/whatsapp/{WEBHOOK_PATH}", content=raw, headers=headers)

    db.expire_all()
    lines = (await db.execute(select(Message))).scalars().all()
    assert len(lines) == 1
    assert len(scheduled) == 1


async def test_a_statuses_only_delivery_is_acknowledged_and_ignored(stage) -> None:
    _, anon, _, _, scheduled, db, _ = stage
    body = {
        "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.1", "status": "read"}]}}]}]
    }
    raw, headers = _signed(body)
    answer = await anon.post(f"/public/whatsapp/{WEBHOOK_PATH}", content=raw, headers=headers)
    assert answer.status_code == 200
    db.expire_all()
    assert (await db.execute(select(Message))).scalars().all() == []
    assert scheduled == []


# --- The answer -----------------------------------------------------------------


async def _delivered(anon, db, text: str = "Do you open on Saturday?") -> tuple[int, int]:
    raw, headers = _signed(_delivery(text))
    assert (
        await anon.post(f"/public/whatsapp/{WEBHOOK_PATH}", content=raw, headers=headers)
    ).status_code == 200
    db.expire_all()
    line = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))
    return line.conversation_id, line.id


async def test_the_answer_reaches_the_customer_and_the_record_in_that_order(stage) -> None:
    _, anon, ids, fake, _, db, app = stage
    conversation_id, message_id = await _delivered(anon, db)

    await transport.respond(app.state.sessionmaker, ids["channel"], message_id)

    assert [(m["to"], m["text"]["body"]) for m in fake.sent] == [("436601234567", GREETING)]
    db.expire_all()
    lines = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller", "agent"]


async def test_a_takeover_between_the_200_and_the_answer_keeps_the_silence(stage) -> None:
    """§A6.7 across the acknowledge-then-answer gap this channel introduces."""
    _, anon, ids, fake, _, db, app = stage
    conversation_id, message_id = await _delivered(anon, db)

    thread = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], message_id)
    assert fake.sent == []


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, anon, ids, fake, _, db, app = stage
    conversation_id, message_id = await _delivered(anon, db)

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], message_id)

    db.expire_all()
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == conversation_id)))
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller"]


# --- The settings card ----------------------------------------------------------


async def test_the_secrets_go_in_together_and_only_masks_come_out(stage) -> None:
    clients, _, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/whatsapp",
        json={"access_token": "EAAG-fresh-token", "app_secret": "fresh-secret"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert "fresh" not in json.dumps(body)
    assert body["access_token_preview"].endswith("oken")
    assert body["app_secret_preview"].endswith("cret")
    # And half a pair is refused - they only work whole.
    refused = await clients["mohamed"].put(
        "/api/channels/whatsapp", json={"access_token": "EAAG-alone"}
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "credentials_incomplete"


async def test_the_card_hands_meta_its_address_and_verify_token(stage) -> None:
    clients, _, _, _, _, _, _ = stage
    read = (await clients["mohamed"].get("/api/channels/whatsapp")).json()
    assert read["callback_url"].endswith(f"/public/whatsapp/{WEBHOOK_PATH}")
    assert read["verify_token"] == VERIFY_TOKEN


async def test_removing_the_credentials_switches_the_channel_off_with_them(stage) -> None:
    clients, _, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/whatsapp", json={"access_token": "", "app_secret": ""}
    )
    assert saved.status_code == 200
    assert saved.json()["enabled"] is False
    assert saved.json()["access_token_preview"] is None

    refused = await clients["mohamed"].put("/api/channels/whatsapp", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "credentials_incomplete"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/whatsapp")).status_code == 200
    refused = await clients["lukas"].put("/api/channels/whatsapp", json={"enabled": False})
    assert refused.status_code == 403


async def test_the_connection_test_names_the_number_and_remembers_it(stage) -> None:
    clients, _, _, _, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/whatsapp/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "ok": True,
        "display_phone_number": "+43 720 111222",
        "verified_name": "Wagner & Partner",
    }
    read = (await clients["mohamed"].get("/api/channels/whatsapp")).json()
    assert read["verified_name"] == "Wagner & Partner"


async def test_refused_credentials_fail_the_test_without_confirming_anything(stage) -> None:
    clients, _, _, fake, _, _, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/whatsapp/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "meta_refused"


# --- The takeover reply, delivered ----------------------------------------------


async def test_a_human_reply_reaches_whatsapp_and_the_record_in_that_order(stage) -> None:
    clients, anon, _, fake, _, db, _ = stage
    conversation_id, _ = await _delivered(anon, db, "I would rather talk to a person.")
    thread = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{conversation_id}/reply",
        json={"text": "Yes — this is Sabine. How can I help?"},
    )
    assert sent.status_code == 201, sent.text
    assert fake.sent[-1]["to"] == "436601234567"
    assert fake.sent[-1]["text"]["body"] == "Yes — this is Sabine. How can I help?"


async def test_an_undelivered_reply_is_not_written_into_the_record(stage) -> None:
    clients, anon, _, fake, _, db, _ = stage
    conversation_id, _ = await _delivered(anon, db)
    thread = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    thread.handling = "human"
    await db.commit()
    before = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))

    fake.refuse = True
    refused = await clients["sabine"].post(
        f"/api/conversations/{conversation_id}/reply", json={"text": "Hello?"}
    )
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "not_delivered"

    db.expire_all()
    last = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))
    assert last.id == before.id
