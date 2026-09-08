"""The SMS channel — a public door, a signed form body, and an empty TwiML answer.

SMS is the first channel built on the declarative contract (D-044), so it writes no
routes: `api/routes/public_channel.py` finds the channel by its address and hands the
request to `receive`, and `api/routes/generic_channel.py` draws the card from `SETUP`.
What is tested here is everything that is the channel's own — the signature, the
acknowledgement the platform expects, dedup by the platform's message id, the
answering policy, and delivery before storage.

The platform is faked over `httpx.MockTransport`: it answers the account resource for
the test button and the messages resource for a send, and it can refuse. The
signature is computed here from the published scheme rather than borrowed from the
module under test, so a change to either side is a failing test and not a silent
agreement.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import generic
from api.channels import sms as transport
from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "cc" * 32

ACCOUNT_SID = "AC00000000000000000000000000d41d"
AUTH_TOKEN = "sms-auth-token-8317"  # noqa: S105
OUR_NUMBER = "+43100000001"
CUSTOMER = "+43100000099"
WEBHOOK_PATH = "sms-door-address-for-the-tests"
DOOR = f"/public/sms/{WEBHOOK_PATH}"

# What the platform is told the door's public address is. The test client speaks to
# the app at this origin, so this is the URL the route itself reconstructs.
PUBLIC_URL = f"http://localhost{DOOR}"


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


class FakeSms:
    """The platform: one account resource, one messages resource, and a refusal."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []
        self.authorisations: list[str] = []
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            return httpx.Response(401, json={"message": "Authenticate"})
        path = request.url.path
        messages = f"/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json"
        if request.method == "POST" and path == messages:
            self.authorisations.append(request.headers.get("Authorization", ""))
            self.sent.append(dict(parse_qsl(request.content.decode())))
            return httpx.Response(201, json={"sid": "SM-outbound"})
        if request.method == "GET" and path == f"/2010-04-01/Accounts/{ACCOUNT_SID}.json":
            return httpx.Response(
                200, json={"sid": ACCOUNT_SID, "friendly_name": "Wagner & Partner"}
            )
        return httpx.Response(404, json={"message": "not found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://sms.test", transport=httpx.MockTransport(self.handler)
        )


def _delivery(
    body: str = "Do you open on Saturday?",
    *,
    sender: str = CUSTOMER,
    message_sid: str = "SM0000000000000000000000000000a1",
) -> dict[str, str]:
    return {
        "MessageSid": message_sid,
        "AccountSid": ACCOUNT_SID,
        "From": sender,
        "To": OUR_NUMBER,
        "Body": body,
        "NumMedia": "0",
    }


def _signature(url: str, params: dict[str, str]) -> str:
    """The published scheme, written out here rather than imported from the module.

    The URL the platform called, then every POST parameter as key and value
    concatenated in key order, HMAC-SHA1 under the auth token, base64.
    """
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(
        AUTH_TOKEN.encode(),
        payload.encode(),
        # The platform's own scheme, not a choice this product makes.
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode()


async def _post(public: AsyncClient, params: dict[str, str], **headers: str) -> httpx.Response:
    signed = {"X-Twilio-Signature": _signature(PUBLIC_URL, params), **headers}
    return await public.post(
        DOOR,
        content=urlencode(params),
        headers={"Content-Type": "application/x-www-form-urlencoded", **signed},
    )


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
        kind="sms",
        name="SMS",
        credentials_encrypted=json.dumps({"auth_token": AUTH_TOKEN}),
        settings_json={"fields": {"account_sid": ACCOUNT_SID, "from_number": OUR_NUMBER}},
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
    fake = FakeSms()
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


def test_a_text_is_answered_and_our_own_number_never_is() -> None:
    assert transport.message_text(_delivery(), OUR_NUMBER) == "Do you open on Saturday?"

    # A text whose sender is the number this channel speaks from is this installation
    # hearing itself; answering it is how two numbers text each other forever.
    echo = _delivery(sender=OUR_NUMBER)
    assert transport.message_text(echo, OUR_NUMBER) is None


def test_a_delivery_without_words_is_not_a_conversation() -> None:
    """A picture with no caption carries nothing for the model to answer."""
    assert transport.message_text(_delivery(""), OUR_NUMBER) is None
    assert transport.message_text(_delivery("   "), OUR_NUMBER) is None
    assert transport.message_text(_delivery(sender=""), OUR_NUMBER) is None


def test_a_long_answer_is_split_on_word_boundaries_and_nothing_is_lost() -> None:
    words = " ".join(f"word{index}" for index in range(400))
    assert len(words) > transport.MESSAGE_MAX

    pieces = transport.split_text(words, transport.MESSAGE_MAX)
    assert len(pieces) > 1
    assert all(len(piece) <= transport.MESSAGE_MAX for piece in pieces)
    assert " ".join(pieces) == words


# --- The door -------------------------------------------------------------------


async def test_a_signed_delivery_is_acknowledged_with_an_empty_twiml_response(
    stage,
) -> None:
    """The answer the platform waits for: an empty document, immediately.

    The reply is generated after the acknowledgement, not inside it — a platform that
    is answered late retries, and the customer gets the same answer twice.
    """
    _, public, _, _, db, _ = stage
    answer = await _post(public, _delivery())

    assert answer.status_code == 200, answer.text
    assert answer.text == '<?xml version="1.0" encoding="UTF-8"?><Response/>'
    assert answer.headers["content-type"].startswith("text/xml")

    await _drain()
    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    lines = (
        (await db.execute(select(Message).where(Message.conversation_id == thread.id)))
        .scalars()
        .all()
    )
    assert [line.speaker for line in lines] == ["caller", "agent"]


async def test_the_answer_is_texted_back_to_the_sender_from_our_own_number(stage) -> None:
    _, public, _, fake, _, _ = stage
    await _post(public, _delivery())
    await _drain()

    assert [(sent["To"], sent["From"], sent["Body"]) for sent in fake.sent] == [
        (CUSTOMER, OUR_NUMBER, GREETING)
    ]
    expected = base64.b64encode(f"{ACCOUNT_SID}:{AUTH_TOKEN}".encode()).decode()
    assert fake.authorisations == [f"Basic {expected}"]


async def test_every_reason_the_door_says_no_reads_exactly_alike(stage) -> None:
    """One refusal for every reason — the door leaks nothing about what is behind it."""
    clients, public, _, _, _, _ = stage
    form = {"Content-Type": "application/x-www-form-urlencoded"}

    refusals = [
        await public.post(
            DOOR,
            content=urlencode(_delivery()),
            headers={**form, "X-Twilio-Signature": "not-the-signature"},
        ),
        await public.post(DOOR, content=urlencode(_delivery()), headers=form),
        await _post(public, _delivery(), **{"X-Twilio-Signature": ""}),
        await public.post(
            "/public/sms/not-an-address", content=urlencode(_delivery()), headers=form
        ),
    ]
    await clients["mohamed"].put("/api/channels/sms", json={"enabled": False})
    refusals.append(await _post(public, _delivery()))

    for refused in refusals:
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_recognised"


async def test_a_signature_computed_over_a_different_address_does_not_open_the_door(
    stage,
) -> None:
    """The URL is part of what is signed, so a delivery cannot be replayed elsewhere."""
    _, public, _, _, _, _ = stage
    params = _delivery()
    elsewhere = _signature("http://localhost/public/sms/some-other-address", params)

    refused = await public.post(
        DOOR,
        content=urlencode(params),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": elsewhere,
        },
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "not_recognised"


async def test_the_public_address_is_read_from_the_proxy_headers_when_they_are_there(
    stage,
) -> None:
    """Behind a reverse proxy the platform called https://…, and that is what it signed."""
    _, public, _, _, _, _ = stage
    params = _delivery()
    outside = f"https://desk.example.test{DOOR}"

    answer = await public.post(
        DOOR,
        content=urlencode(params),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "desk.example.test",
            "X-Twilio-Signature": _signature(outside, params),
        },
    )
    assert answer.status_code == 200, answer.text
    await _drain()


async def test_a_repeated_message_sid_is_dropped(stage) -> None:
    """Retries are normal; a customer must not be answered twice for one text."""
    _, public, _, fake, db, _ = stage
    assert (await _post(public, _delivery())).status_code == 200
    await _drain()
    assert (await _post(public, _delivery())).status_code == 200
    await _drain()

    db.expire_all()
    lines = (await db.execute(select(Message))).scalars().all()
    assert [line.speaker for line in lines] == ["caller", "agent"]
    assert len(fake.sent) == 1


async def test_a_signed_get_is_acknowledged_the_same_way(stage) -> None:
    """Some accounts are configured to call the door with GET; the answer is the same."""
    _, public, _, _, _, _ = stage
    answer = await public.get(DOOR, headers={"X-Twilio-Signature": _signature(PUBLIC_URL, {})})
    assert answer.status_code == 200, answer.text
    assert answer.text == '<?xml version="1.0" encoding="UTF-8"?><Response/>'


# --- Ingest and the answer ------------------------------------------------------


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _delivery())
    assert needs_reply is not None

    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)
    assert fake.sent == []

    follow_up = _delivery("So is anyone there?", message_sid="SM-second")
    assert await transport.ingest(db, channel, follow_up) is None


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    """Delivery before storage: a transcript never shows an answer nobody received."""
    _, _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    needs_reply = await transport.ingest(db, channel, _delivery())

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], needs_reply)

    db.expire_all()
    assert [line.speaker for line in (await db.execute(select(Message))).scalars().all()] == [
        "caller"
    ]


async def test_an_answer_over_the_limit_reaches_the_customer_in_several_texts(stage) -> None:
    """The cut is `generic.deliver`'s, so an agent's answer and a person's divide alike."""
    _, _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    long_answer = " ".join(f"word{index}" for index in range(400))

    async with transport.make_client() as client:
        await generic.deliver(
            transport, client, generic.credentials_of(channel), CUSTOMER, long_answer
        )

    assert len(fake.sent) > 1
    assert " ".join(sent["Body"] for sent in fake.sent) == long_answer


# --- The card -------------------------------------------------------------------


async def test_the_card_declares_its_fields_and_its_public_address(stage) -> None:
    clients, _, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/sms")).json()

    assert body["setup"]["title"] == "SMS"
    assert [field["name"] for field in body["setup"]["fields"]] == [
        "account_sid",
        "auth_token",
        "from_number",
    ]
    assert [field["secret"] for field in body["setup"]["fields"]] == [False, True, False]
    assert body["verified_live"] is True
    assert body["webhook_url"] == PUBLIC_URL
    assert body["values"] == {"account_sid": ACCOUNT_SID, "from_number": OUR_NUMBER}


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"auth_token": "a-fresh-auth-token-4417"}}
    )
    assert saved.status_code == 200, saved.text
    assert "a-fresh-auth-token-4417" not in json.dumps(saved.json())
    assert saved.json()["previews"]["auth_token"].endswith("4417")
    assert saved.json()["values"]["account_sid"] == ACCOUNT_SID


async def test_removing_the_secret_switches_the_channel_off_with_it(stage) -> None:
    clients, _, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"auth_token": ""}}
    )
    assert cleared.json()["previews"]["auth_token"] is None
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    refused = await clients["mohamed"].put("/api/channels/sms", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/sms")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/sms", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/sms/test")).status_code == 403


async def test_the_test_button_reports_the_account_the_platform_names(stage) -> None:
    clients, _, _, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/sms/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": "Wagner & Partner"}
    assert (await clients["mohamed"].get("/api/channels/sms")).json()[
        "identity"
    ] == "Wagner & Partner"


async def test_a_refused_credential_names_the_kind_and_confirms_nothing_else(stage) -> None:
    clients, _, _, fake, _, _ = stage
    fake.refuse = True
    answer = await clients["mohamed"].post("/api/channels/sms/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "sms_refused"


# --- One public address, for the card and for the door --------------------------


def _configure_base(app, base: str | None) -> None:
    """Point this installation at a public address, the way `PUBLIC_BASE_URL` does."""
    app.state.settings = app.state.settings.model_copy(update={"public_base_url": base})


async def test_the_card_prints_the_configured_public_address(stage) -> None:
    """What the operator pastes into the platform is what the door will check."""
    clients, _, _, _, _, app = stage
    _configure_base(app, "https://desk.example.test")

    body = (await clients["mohamed"].get("/api/channels/sms")).json()
    assert body["webhook_url"] == f"https://desk.example.test{DOOR}"


async def test_a_delivery_signed_over_the_configured_address_is_accepted(stage) -> None:
    """The card and the door agree by construction, with no proxy header in sight."""
    _, public, _, _, _, app = stage
    _configure_base(app, "https://desk.example.test/")
    params = _delivery()

    answer = await public.post(
        DOOR,
        content=urlencode(params),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Twilio-Signature": _signature(f"https://desk.example.test{DOOR}", params),
        },
    )
    assert answer.status_code == 200, answer.text
    await _drain()


async def test_the_configured_address_wins_over_the_proxy_headers(stage) -> None:
    """A forwarded header is only read where nothing better was configured.

    With `PUBLIC_BASE_URL` set, a stranger who sends their own `X-Forwarded-Host`
    cannot move the address the signature is checked against.
    """
    _, public, _, _, _, app = stage
    _configure_base(app, "https://desk.example.test")
    params = _delivery()

    refused = await public.post(
        DOOR,
        content=urlencode(params),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "attacker.example.test",
            "X-Twilio-Signature": _signature(f"https://attacker.example.test{DOOR}", params),
        },
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "not_recognised"


async def test_a_signature_header_that_is_not_ascii_is_refused_and_not_a_failure(
    stage,
) -> None:
    """`hmac.compare_digest` raises on a non-ASCII `str`; the door owes one refusal."""
    _, public, _, _, _, _ = stage
    refused = await public.post(
        DOOR,
        content=urlencode(_delivery()),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            # Sent as bytes: the header never was text, and this is the shape a
            # stranger can actually put on the wire.
            "X-Twilio-Signature": "التوقيع".encode(),
        },
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "not_recognised"


async def test_an_older_delivery_repeated_after_a_newer_one_is_still_dropped(stage) -> None:
    """Dedup is a ring, not one slot: a platform does not retry in order."""
    _, _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])

    first = _delivery("Are you open?", message_sid="SM-first")
    second = _delivery("Anyone there?", message_sid="SM-second")
    assert await transport.ingest(db, channel, first) is not None
    assert await transport.ingest(db, channel, second) is not None

    assert await transport.ingest(db, channel, first) is None
    assert await transport.ingest(db, channel, second) is None

    db.expire_all()
    lines = (await db.execute(select(Message))).scalars().all()
    assert [line.text for line in lines] == ["Are you open?", "Anyone there?"]


# --- The takeover reply, delivered ----------------------------------------------


async def _taken_over_thread(clients, db, ids) -> int:
    """One customer's thread, with a person holding it — the id, not the row.

    The row is expired the moment the route commits, and reading an expired column
    from this session would be database work in the wrong place.
    """
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _delivery("I would rather talk to a person."))
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    thread_id = thread.id
    taken = await clients["sabine"].post(f"/api/conversations/{thread_id}/takeover")
    assert taken.status_code == 200, taken.text
    db.expire_all()
    return thread_id


async def test_a_human_reply_is_delivered_before_it_is_stored(stage) -> None:
    """A declarative channel needs no branch of its own for a person to answer on it."""
    clients, _, ids, fake, db, _ = stage
    thread_id = await _taken_over_thread(clients, db, ids)

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread_id}/reply",
        json={"text": "Yes — this is Sabine. How can I help?"},
    )
    assert sent.status_code == 201, sent.text
    assert (fake.sent[-1]["To"], fake.sent[-1]["From"]) == (CUSTOMER, OUR_NUMBER)
    assert fake.sent[-1]["Body"] == "Yes — this is Sabine. How can I help?"

    db.expire_all()
    lines = (await db.execute(select(Message))).scalars().all()
    assert lines[-1].speaker == "human"


async def test_a_human_reply_that_is_refused_is_not_stored(stage) -> None:
    """Delivery before storage holds for a person's words exactly as for the agent's."""
    clients, _, ids, fake, db, _ = stage
    thread_id = await _taken_over_thread(clients, db, ids)
    before = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))

    fake.refuse = True
    refused = await clients["sabine"].post(
        f"/api/conversations/{thread_id}/reply", json={"text": "Hello?"}
    )
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "not_delivered"

    db.expire_all()
    last = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))
    assert last.id == before.id
