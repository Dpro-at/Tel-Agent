"""The Microsoft Teams channel — a public door, a bearer token, and an activity JSON.

Teams is the second channel on the declarative contract (D-044), so it writes no routes:
`api/routes/public_channel.py` finds the channel by its address and hands the request to
`receive`, and `api/routes/generic_channel.py` draws the card from `SETUP`. What is
tested here is everything the channel owns — the token the platform proves itself with,
the answering policy in a group conversation, dedup by the activity id, the address the
answer is posted back to, and the outbound token being fetched once rather than per
message.

The platform is faked over `httpx.MockTransport`: it serves the OpenID metadata
document, the key set named by it, the login endpoint that issues the outbound token,
and the conversations endpoint an answer is posted to. Inbound activities are signed
here with the fake key, so nothing in this file borrows the module's own idea of what a
valid token looks like.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import generic
from api.channels import teams as transport
from api.channels.jwt import reset_jwks_cache, sign_rs256
from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "dd" * 32

APP_ID = "6a1f0c2e-4f2b-4a1d-9f3e-1c8d2b7a5e40"
APP_PASSWORD = "teams-client-secret-9471"  # noqa: S105
BOT_ID = f"28:{APP_ID}"
CUSTOMER_ID = "29:1cUsTomEr-outside-the-tenant"
CONVERSATION_ID = "a:19-conversation-0091"
SERVICE_URL = "https://serviceurl.teams.test/emea/"

# Where an answer on this thread is posted, per the connector's own address scheme.
ACTIVITIES_PATH = f"/emea/v3/conversations/{quote(CONVERSATION_ID, safe='')}/activities"
ACTIVITIES_URL = f"https://serviceurl.teams.test{ACTIVITIES_PATH}"

# The platform's own addresses, written out here rather than imported from the module.
OPENID_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"
ISSUER = "https://api.botframework.com"
LOGIN_ENDPOINT = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
KID = "botframework-key-1"

WEBHOOK_PATH = "teams-door-address-for-the-tests"
DOOR = f"/public/teams/{WEBHOOK_PATH}"

# One key for the whole file: generating a 2048-bit RSA key per test is a second each.
_KEY_PEM: str | None = None


def _private_key_pem() -> str:
    global _KEY_PEM
    if _KEY_PEM is None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _KEY_PEM = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    return _KEY_PEM


def _jwk(kid: str) -> dict[str, str]:
    """The public half of the signing key, the way an issuer publishes it."""
    public = (
        load_pem_private_key(_private_key_pem().encode(), password=None)
        .public_key()
        .public_numbers()
    )

    def number(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": number(public.n),
        "e": number(public.e),
    }


def _bearer(*, audience: str = APP_ID, issuer: str = ISSUER, seconds: int = 300) -> str:
    """One inbound token, signed with the key the fake platform publishes."""
    now = dt.datetime.now(tz=dt.UTC).timestamp()
    claims = {
        "iss": issuer,
        "aud": audience,
        "exp": int(now + seconds),
        "iat": int(now),
        "serviceurl": SERVICE_URL,
    }
    return sign_rs256(claims, _private_key_pem(), KID)


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


@pytest.fixture(autouse=True)
def empty_caches():
    """Both caches are process-wide, so one test must not answer for the next."""
    reset_jwks_cache()
    transport.reset_token_cache()
    yield
    reset_jwks_cache()
    transport.reset_token_cache()


class FakeTeams:
    """The platform: its metadata, its key set, its login endpoint, its conversations."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.authorisations: list[str] = []
        self.tokens_issued = 0
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and url == OPENID_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "jwks_uri": JWKS_URL,
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
        if request.method == "GET" and url == JWKS_URL:
            return httpx.Response(200, json={"keys": [_jwk(KID)]})
        if request.method == "POST" and url == LOGIN_ENDPOINT:
            if self.refuse:
                return httpx.Response(401, json={"error": "invalid_client"})
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "access_token": f"outbound-token-{self.tokens_issued}",
                },
            )
        if request.method == "POST" and request.url.path.endswith("/activities"):
            if self.refuse:
                return httpx.Response(401, json={"error": {"code": "Unauthorized"}})
            self.authorisations.append(request.headers.get("Authorization", ""))
            self.sent.append({"url": url, **json.loads(request.content.decode())})
            return httpx.Response(201, json={"id": f"answer-{len(self.sent)}"})
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _mention(text: str = "<at>Reception</at>") -> dict[str, Any]:
    return {"type": "mention", "text": text, "mentioned": {"id": BOT_ID, "name": "Reception"}}


def _activity(
    text: str = "Do you open on Saturday?",
    *,
    activity_id: str = "activity-0001",
    sender: str = CUSTOMER_ID,
    kind: str = "message",
    group: bool = False,
    entities: list[dict[str, Any]] | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    activity: dict[str, Any] = {
        "type": kind,
        "id": activity_id,
        "timestamp": "2026-09-08T10:00:00.000Z",
        "serviceUrl": SERVICE_URL,
        "channelId": "msteams",
        "from": {"id": sender, "name": "Anna Berger", "aadObjectId": "outside"},
        "recipient": {"id": BOT_ID, "name": "Reception"},
        "conversation": {"id": CONVERSATION_ID, "conversationType": "channel"},
        "text": text,
    }
    if group:
        activity["conversation"]["isGroup"] = True
    else:
        activity["conversation"]["conversationType"] = "personal"
    if entities is not None:
        activity["entities"] = entities
    if role is not None:
        activity["from"]["role"] = role
    return activity


async def _post(
    public: AsyncClient, activity: dict[str, Any], **headers: str
) -> httpx.Response:
    sent = {"Authorization": f"Bearer {_bearer()}", **headers}
    return await public.post(DOOR, json=activity, headers=sent)


async def _drain() -> None:
    """Let the replies the door scheduled finish, so nothing is asserted mid-flight."""
    pending = list(generic._REPLIES)
    if pending:
        await asyncio.gather(*pending)


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="teams",
        name="Microsoft Teams",
        credentials_encrypted=json.dumps({"app_password": APP_PASSWORD}),
        settings_json={"fields": {"app_id": APP_ID}},
        webhook_path=WEBHOOK_PATH,
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
    fake = FakeTeams()
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


async def _channel_row(db: AsyncSession, channel_id: int) -> Channel:
    db.expire_all()
    return await db.scalar(select(Channel).where(Channel.id == channel_id))


# --- The answering policy -------------------------------------------------------


def test_a_personal_message_is_answered_and_the_bot_never_answers_itself() -> None:
    assert transport.message_text(_activity(), APP_ID) == "Do you open on Saturday?"

    # The connector delivers the bot's own activities back to it in some conversation
    # shapes, and a bot answering itself is a conversation with no end.
    assert transport.message_text(_activity(sender=BOT_ID), APP_ID) is None
    assert transport.message_text(_activity(role="bot"), APP_ID) is None


def test_a_group_message_is_answered_only_when_the_bot_is_addressed() -> None:
    """A channel or group chat carries other people's conversation, not ours."""
    overheard = _activity("Shall we meet at four?", group=True)
    assert transport.message_text(overheard, APP_ID) is None

    addressed = _activity(
        "<at>Reception</at> do you open on Saturday?", group=True, entities=[_mention()]
    )
    assert transport.message_text(addressed, APP_ID) == "do you open on Saturday?"


def test_the_mention_is_stripped_before_the_text_reaches_the_model() -> None:
    """The model must read the customer's question, not the markup around our name."""
    written = _activity(
        "<at>Reception</at> is <at>Reception</at> open on Saturday?",
        group=True,
        entities=[_mention()],
    )
    assert transport.message_text(written, APP_ID) == "is open on Saturday?"


def test_an_activity_that_is_not_a_message_carries_nothing_to_answer() -> None:
    assert transport.message_text(_activity(kind="conversationUpdate"), APP_ID) is None
    assert transport.message_text(_activity(kind="typing"), APP_ID) is None
    assert transport.message_text(_activity(""), APP_ID) is None
    assert transport.message_text(_activity("<at>Reception</at>"), APP_ID) is None


def test_a_long_answer_is_split_on_word_boundaries_and_nothing_is_lost() -> None:
    words = " ".join(f"word{index}" for index in range(900))
    assert len(words) > transport.MESSAGE_MAX

    pieces = transport.split_text(words, transport.MESSAGE_MAX)
    assert len(pieces) > 1
    assert all(len(piece) <= transport.MESSAGE_MAX for piece in pieces)
    assert " ".join(pieces) == words


# --- The door -------------------------------------------------------------------


async def test_a_signed_activity_is_acknowledged_and_answered_afterwards(stage) -> None:
    """200 immediately: the connector retries a webhook it is answered late."""
    _, public, _, _, db, _ = stage
    answer = await _post(public, _activity())

    assert answer.status_code == 200, answer.text

    await _drain()
    db.expire_all()
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CONVERSATION_ID)
    )
    assert thread is not None
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == thread.id)))
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller", "agent"]


async def test_the_answer_is_posted_to_the_conversation_the_customer_wrote_in(stage) -> None:
    _, public, _, fake, _, _ = stage
    await _post(public, _activity())
    await _drain()

    assert [(sent["url"], sent["type"], sent["text"]) for sent in fake.sent] == [
        (ACTIVITIES_URL, "message", GREETING)
    ]
    assert fake.authorisations == ["Bearer outbound-token-1"]


async def test_every_reason_the_door_says_no_reads_exactly_alike(stage) -> None:
    """One refusal for every reason — the door leaks nothing about what is behind it."""
    clients, public, _, _, _, _ = stage

    refusals = [
        # No proof of identity at all.
        await public.post(DOOR, json=_activity()),
        # A token that was never signed.
        await public.post(
            DOOR, json=_activity(), headers={"Authorization": "Bearer not.a.token"}
        ),
        # A token signed for a different application.
        await _post(
            public,
            _activity(),
            Authorization=f"Bearer {_bearer(audience='another-application')}",
        ),
        # A token issued by somebody else.
        await _post(
            public,
            _activity(),
            Authorization=f"Bearer {_bearer(issuer='https://issuer.example.invalid')}",
        ),
        # A token that has expired.
        await _post(public, _activity(), Authorization=f"Bearer {_bearer(seconds=-600)}"),
        # A scheme this door does not speak.
        await _post(public, _activity(), Authorization="Basic bm90OmEtdG9rZW4="),
        # An address nothing is listening on.
        await public.post("/public/teams/not-an-address", json=_activity()),
    ]
    await clients["mohamed"].put("/api/channels/teams", json={"enabled": False})
    refusals.append(await _post(public, _activity()))

    for refused in refusals:
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_recognised"


async def test_a_body_that_is_not_an_activity_is_refused_like_everything_else(stage) -> None:
    _, public, _, _, _, _ = stage
    refused = await public.post(
        DOOR,
        content=b"this is not JSON",
        headers={"Authorization": f"Bearer {_bearer()}", "Content-Type": "application/json"},
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "not_recognised"


async def test_a_conversation_update_is_acknowledged_and_stores_nothing(stage) -> None:
    """Somebody adding the bot to a team is news, not a conversation."""
    _, public, _, fake, db, _ = stage
    answer = await _post(public, _activity(kind="conversationUpdate", text=""))

    assert answer.status_code == 200, answer.text
    await _drain()
    db.expire_all()
    assert (await db.execute(select(Conversation))).scalars().all() == []
    assert (await db.execute(select(Message))).scalars().all() == []
    assert fake.sent == []


async def test_a_repeated_activity_id_is_dropped(stage) -> None:
    """Retries are normal; a customer must not be answered twice for one message."""
    _, public, _, fake, db, _ = stage
    assert (await _post(public, _activity())).status_code == 200
    await _drain()
    assert (await _post(public, _activity())).status_code == 200
    await _drain()

    db.expire_all()
    lines = (await db.execute(select(Message))).scalars().all()
    assert [line.speaker for line in lines] == ["caller", "agent"]
    assert len(fake.sent) == 1


# --- Ingest and the answer ------------------------------------------------------


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _activity())
    assert needs_reply is not None

    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CONVERSATION_ID)
    )
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)
    assert fake.sent == []

    follow_up = _activity("So is anyone there?", activity_id="activity-0002")
    assert await transport.ingest(db, channel, follow_up) is None


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    """Delivery before storage: a transcript never shows an answer nobody received."""
    _, _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _activity())

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    db.expire_all()
    assert [line.speaker for line in (await db.execute(select(Message))).scalars().all()] == [
        "caller"
    ]


async def test_the_outbound_token_is_fetched_once_and_reused_for_the_next_message(
    stage,
) -> None:
    """A token is good for an hour; asking for one per message is a call per message."""
    _, public, _, fake, _, _ = stage
    assert (await _post(public, _activity())).status_code == 200
    await _drain()
    assert (
        await _post(public, _activity("And on Sunday?", activity_id="activity-0002"))
    ).status_code == 200
    await _drain()

    assert len(fake.sent) == 2
    assert fake.tokens_issued == 1
    assert fake.authorisations == ["Bearer outbound-token-1", "Bearer outbound-token-1"]


async def test_an_answer_over_the_limit_reaches_the_customer_in_several_activities(
    stage,
) -> None:
    """The cut is `generic.deliver`'s, so an agent's answer and a person's divide alike."""
    _, _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _activity())
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CONVERSATION_ID)
    )
    long_answer = " ".join(f"word{index}" for index in range(900))

    async with transport.make_client() as client:
        await generic.deliver(
            transport,
            client,
            generic.credentials_of(channel),
            transport.reply_target(thread),
            long_answer,
        )

    assert len(fake.sent) > 1
    assert " ".join(sent["text"] for sent in fake.sent) == long_answer


async def test_the_thread_remembers_where_an_answer_has_to_be_posted(stage) -> None:
    """`serviceUrl` arrives with the message and nowhere else, so it is kept."""
    _, _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _activity())

    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CONVERSATION_ID)
    )
    assert transport.reply_target(thread) == ACTIVITIES_URL


# --- The card -------------------------------------------------------------------


async def test_the_card_declares_its_fields_and_its_public_address(stage) -> None:
    clients, _, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/teams")).json()

    assert body["setup"]["title"] == "Microsoft Teams"
    assert [field["name"] for field in body["setup"]["fields"]] == [
        "app_id",
        "app_password",
        "tenant_id",
    ]
    assert [field["secret"] for field in body["setup"]["fields"]] == [False, True, False]
    assert [field["required"] for field in body["setup"]["fields"]] == [True, True, False]
    assert body["setup"]["note"] == (
        "Answers customers who reach your Teams bot from outside your organisation. "
        "Internal chat is not a channel."
    )
    assert body["verified_live"] is True
    assert body["webhook_url"] == f"http://localhost{DOOR}"
    assert body["values"] == {"app_id": APP_ID}


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/teams", json={"fields": {"app_password": "a-fresh-client-secret-4417"}}
    )
    assert saved.status_code == 200, saved.text
    assert "a-fresh-client-secret-4417" not in json.dumps(saved.json())
    assert saved.json()["previews"]["app_password"].endswith("4417")
    assert saved.json()["values"]["app_id"] == APP_ID


async def test_removing_the_secret_switches_the_channel_off_with_it(stage) -> None:
    clients, _, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/teams", json={"fields": {"app_password": ""}}
    )
    assert cleared.json()["previews"]["app_password"] is None
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    refused = await clients["mohamed"].put("/api/channels/teams", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/teams")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/teams", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/teams/test")).status_code == 403


async def test_the_test_button_takes_a_token_and_reports_the_application(stage) -> None:
    """The framework has no "who am I" call, so the application it issued for is it."""
    clients, _, _, fake, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/teams/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": APP_ID}
    assert fake.tokens_issued == 1
    assert (await clients["mohamed"].get("/api/channels/teams")).json()["identity"] == APP_ID


async def test_a_refused_credential_names_the_kind_and_confirms_nothing_else(stage) -> None:
    clients, _, _, fake, _, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/teams/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "teams_refused"


async def test_a_single_tenant_application_asks_its_own_tenant_for_the_token(stage) -> None:
    """A single-tenant registration is issued tokens by its directory, not the default."""
    _, _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    credentials = {**generic.credentials_of(channel), "tenant_id": "contoso.onmicrosoft.test"}

    seen: list[str] = []
    original = fake.handler

    def watching(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "oauth2" in request.url.path:
            seen.append(str(request.url))
            return httpx.Response(
                200, json={"access_token": "tenant-token", "expires_in": 3600}
            )
        return original(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(watching)) as client:
        assert await transport.probe(client, credentials) == APP_ID

    assert seen == [
        "https://login.microsoftonline.com/contoso.onmicrosoft.test/oauth2/v2.0/token"
    ]


# --- The takeover reply, delivered ----------------------------------------------


async def test_a_human_reply_is_delivered_before_it_is_stored(stage) -> None:
    """A declarative channel needs no branch of its own for a person to answer on it."""
    clients, _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _activity("I would rather talk to a person."))
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == CONVERSATION_ID)
    )
    thread_id = thread.id
    taken = await clients["sabine"].post(f"/api/conversations/{thread_id}/takeover")
    assert taken.status_code == 200, taken.text
    db.expire_all()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread_id}/reply",
        json={"text": "Yes — this is Sabine. How can I help?"},
    )
    assert sent.status_code == 201, sent.text
    assert fake.sent[-1]["url"] == ACTIVITIES_URL
    assert fake.sent[-1]["text"] == "Yes — this is Sabine. How can I help?"

    db.expire_all()
    lines = (await db.execute(select(Message))).scalars().all()
    assert lines[-1].speaker == "human"
