"""Messenger and Instagram — one transport, two kinds, one door for Meta.

Most tests are parametrized over both kinds, because the contract is one contract:
the WhatsApp door's guards on the shared `/public/meta/{path}` address, the
acknowledge-then-answer split, the takeover silence, delivery before storage. What
this pair adds and the tests pin: **the echo guard** — a subscribed page is told
about its own outbound messages, and answering them is how a page argues with
itself forever — and the `object` word check, so a delivery for one product never
lands in the other product's channel.
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
from api.channels import meta_chat as transport
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
ACCESS_TOKEN = "EAAG-page-access-token"  # noqa: S105
APP_SECRET = "meta-app-secret"  # noqa: S105
ACCOUNT_ID = "17840012345"
VERIFY_TOKEN = "verify-me-please"  # noqa: S105

PATHS = {"messenger": "ms-" + "a" * 28, "instagram": "ig-" + "b" * 28}


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
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            return httpx.Response(401, json={"error": {"message": "Invalid OAuth token"}})
        if request.method == "POST" and request.url.path.endswith("/me/messages"):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"message_id": "mid.out"})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": ACCOUNT_ID,
                    "name": "Wagner & Partner",
                    "username": "wagnerpartner",
                },
            )
        return httpx.Response(404, json={"error": {"message": "unknown"}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://graph.test", transport=httpx.MockTransport(self.handler)
        )


def _delivery(
    kind: str,
    text: str = "Do you open on Saturday?",
    *,
    sender: str = "24400001",
    mid: str = "mid.1",
) -> dict:
    return {
        "object": transport.OBJECT_FOR_KIND[kind],
        "entry": [
            {
                "id": ACCOUNT_ID,
                "messaging": [
                    {
                        "sender": {"id": sender},
                        "recipient": {"id": ACCOUNT_ID},
                        "message": {"mid": mid, "text": text},
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
    """One workspace with an active channel of each kind, and the fake platform."""
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    ids: dict[str, int] = {"workspace": mine.id}
    for kind in transport.KINDS:
        channel = Channel(
            workspace_id=mine.id,
            kind=kind,
            name=kind.capitalize(),
            webhook_path=PATHS[kind],
            credentials_encrypted=json.dumps(
                {"access_token": ACCESS_TOKEN, "app_secret": APP_SECRET}
            ),
            settings_json={"account_id": ACCOUNT_ID, "verify_token": VERIFY_TOKEN},
            status="active",
        )
        migrated.add(channel)
        await migrated.flush()
        ids[kind] = channel.id

    for username, role in (("mohamed", "admin"), ("sabine", "reception"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role=role))
    await migrated.commit()

    fake = FakeGraph()
    monkeypatch.setattr(transport, "make_client", fake.client)

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


# --- The shared door ------------------------------------------------------------


@pytest.mark.parametrize("kind", transport.KINDS)
async def test_the_handshake_echoes_the_challenge_for_the_right_token(stage, kind) -> None:
    _, anon, _, _, _, _, _ = stage
    answer = await anon.get(
        f"/public/meta/{PATHS[kind]}",
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
        f"/public/meta/{PATHS['messenger']}",
        params={"hub.mode": "subscribe", "hub.verify_token": "guess", "hub.challenge": "x"},
    )
    unknown = await anon.get(
        "/public/meta/no-such-address",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "x",
        },
    )
    assert wrong.status_code == unknown.status_code == 403
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


@pytest.mark.parametrize("kind", transport.KINDS)
async def test_a_signed_delivery_is_stored_and_a_reply_is_scheduled(stage, kind) -> None:
    _, anon, ids, _, scheduled, db, _ = stage
    raw, headers = _signed(_delivery(kind))
    answer = await anon.post(f"/public/meta/{PATHS[kind]}", content=raw, headers=headers)
    assert answer.status_code == 200

    db.expire_all()
    thread = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == ids[kind], Conversation.external_id == "24400001"
        )
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
    assert (ids[kind], lines[0].id) in scheduled


async def test_a_bad_signature_answers_like_an_unknown_address_and_stores_nothing(
    stage,
) -> None:
    _, anon, _, _, scheduled, db, _ = stage
    raw = json.dumps(_delivery("messenger")).encode()
    forged = await anon.post(
        f"/public/meta/{PATHS['messenger']}",
        content=raw,
        headers={
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "Content-Type": "application/json",
        },
    )
    unknown = await anon.post("/public/meta/no-such-address", content=raw)
    assert forged.status_code == unknown.status_code == 403
    assert forged.json()["error"]["code"] == unknown.json()["error"]["code"]

    db.expire_all()
    assert (await db.execute(select(Message))).scalars().all() == []
    assert scheduled == []


async def test_the_channels_own_echo_is_dropped_before_it_becomes_a_conversation(
    stage,
) -> None:
    """The rule this pair adds: a page told about its own message must not answer it."""
    _, anon, _, _, scheduled, db, _ = stage
    raw, headers = _signed(_delivery("messenger", sender=ACCOUNT_ID))
    answer = await anon.post(f"/public/meta/{PATHS['messenger']}", content=raw, headers=headers)
    assert answer.status_code == 200

    db.expire_all()
    assert (await db.execute(select(Conversation))).scalars().all() == []
    assert scheduled == []


async def test_a_delivery_for_the_other_product_is_ignored_whole(stage) -> None:
    """An `instagram` delivery to the Messenger channel's address stores nothing:
    the `object` word is checked against what the row says it is."""
    _, anon, _, _, scheduled, db, _ = stage
    raw, headers = _signed(_delivery("instagram"))
    answer = await anon.post(f"/public/meta/{PATHS['messenger']}", content=raw, headers=headers)
    assert answer.status_code == 200
    db.expire_all()
    assert (await db.execute(select(Message))).scalars().all() == []
    assert scheduled == []


async def test_a_retried_delivery_is_dropped_by_metas_own_message_id(stage) -> None:
    _, anon, _, _, scheduled, db, _ = stage
    raw, headers = _signed(_delivery("instagram"))
    await anon.post(f"/public/meta/{PATHS['instagram']}", content=raw, headers=headers)
    await anon.post(f"/public/meta/{PATHS['instagram']}", content=raw, headers=headers)

    db.expire_all()
    assert len((await db.execute(select(Message))).scalars().all()) == 1
    assert len(scheduled) == 1


# --- The answer -----------------------------------------------------------------


async def _delivered(
    anon, db, kind: str, text: str = "Do you open on Saturday?"
) -> tuple[int, int]:
    raw, headers = _signed(_delivery(kind, text))
    assert (
        await anon.post(f"/public/meta/{PATHS[kind]}", content=raw, headers=headers)
    ).status_code == 200
    db.expire_all()
    line = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))
    return line.conversation_id, line.id


@pytest.mark.parametrize("kind", transport.KINDS)
async def test_the_answer_reaches_the_customer_and_the_record_in_that_order(
    stage, kind
) -> None:
    _, anon, ids, fake, _, db, app = stage
    conversation_id, message_id = await _delivered(anon, db, kind)

    await transport.respond(app.state.sessionmaker, ids[kind], message_id)

    assert [(m["recipient"]["id"], m["message"]["text"]) for m in fake.sent] == [
        ("24400001", GREETING)
    ]
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
    _, anon, ids, fake, _, db, app = stage
    conversation_id, message_id = await _delivered(anon, db, "messenger")

    thread = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["messenger"], message_id)
    assert fake.sent == []


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, anon, ids, fake, _, db, app = stage
    conversation_id, message_id = await _delivered(anon, db, "instagram")

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["instagram"], message_id)

    db.expire_all()
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == conversation_id)))
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller"]


# --- The settings cards ----------------------------------------------------------


@pytest.mark.parametrize("kind", transport.KINDS)
async def test_the_secrets_go_in_together_and_only_masks_come_out(stage, kind) -> None:
    clients, _, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        f"/api/channels/{kind}",
        json={"access_token": "EAAG-fresh-token", "app_secret": "fresh-secret"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert "fresh" not in json.dumps(body)
    assert body["access_token_preview"].endswith("oken")
    refused = await clients["mohamed"].put(
        f"/api/channels/{kind}", json={"access_token": "EAAG-alone"}
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "credentials_incomplete"


@pytest.mark.parametrize("kind", transport.KINDS)
async def test_the_card_hands_meta_its_address_and_verify_token(stage, kind) -> None:
    clients, _, _, _, _, _, _ = stage
    read = (await clients["mohamed"].get(f"/api/channels/{kind}")).json()
    assert read["callback_url"].endswith(f"/public/meta/{PATHS[kind]}")
    assert read["verify_token"] == VERIFY_TOKEN


@pytest.mark.parametrize("kind", transport.KINDS)
async def test_removing_the_credentials_switches_the_channel_off_with_them(stage, kind) -> None:
    clients, _, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        f"/api/channels/{kind}", json={"access_token": "", "app_secret": ""}
    )
    assert saved.status_code == 200
    assert saved.json()["enabled"] is False

    refused = await clients["mohamed"].put(f"/api/channels/{kind}", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "credentials_incomplete"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _, _, _ = stage
    for kind in transport.KINDS:
        assert (await clients["lukas"].get(f"/api/channels/{kind}")).status_code == 200
        refused = await clients["lukas"].put(f"/api/channels/{kind}", json={"enabled": False})
        assert refused.status_code == 403


async def test_the_connection_test_names_the_page_and_the_account(stage) -> None:
    """Messenger reads the page's name; Instagram reads the account's username."""
    clients, _, _, _, _, _, _ = stage
    page = await clients["mohamed"].post("/api/channels/messenger/test")
    assert page.status_code == 200, page.text
    assert page.json()["account_name"] == "Wagner & Partner"

    account = await clients["mohamed"].post("/api/channels/instagram/test")
    assert account.status_code == 200, account.text
    read = (await clients["mohamed"].get("/api/channels/instagram")).json()
    assert read["account_name"] is not None


async def test_refused_credentials_fail_the_test_without_confirming_anything(stage) -> None:
    clients, _, _, fake, _, _, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/messenger/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "meta_refused"


# --- The takeover reply, delivered ------------------------------------------------


@pytest.mark.parametrize("kind", transport.KINDS)
async def test_a_human_reply_reaches_meta_and_the_record_in_that_order(stage, kind) -> None:
    clients, anon, _, fake, _, db, _ = stage
    conversation_id, _ = await _delivered(anon, db, kind, "I would rather talk to a person.")
    thread = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{conversation_id}/reply",
        json={"text": "Yes — this is Sabine. How can I help?"},
    )
    assert sent.status_code == 201, sent.text
    assert fake.sent[-1]["recipient"]["id"] == "24400001"
    assert fake.sent[-1]["message"]["text"] == "Yes — this is Sabine. How can I help?"


async def test_an_undelivered_reply_is_not_written_into_the_record(stage) -> None:
    clients, anon, _, fake, _, db, _ = stage
    conversation_id, _ = await _delivered(anon, db, "messenger")
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
