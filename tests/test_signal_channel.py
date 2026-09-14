"""The Signal channel — a number on the customer's REST bridge, and its settings card.

What these tests own is everything the receive loop hands work to: `message_text` (the
answering policy: one-to-one always, groups only when mentioned, the number's own
messages never), `receive_once` (what is asked of the bridge and what comes back),
`ingest` (storage, dedup, backlog, rules, takeover silence), `respond` (delivery before
storage, against a fake bridge), the supervisor's reconcile pass, the address rule, and
the generic card's contract for a channel with no secret at all.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import generic
from api.channels import signal as transport
from api.channels.generic import ChannelRefused
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
BRIDGE = "http://127.0.0.1:8080"
# Numbers from the range reserved for fiction; none of them reaches a person.
NUMBER = "+15550100001"
CUSTOMER = "+15550100002"
SOMEONE_ELSE = "+15550100003"
CUSTOMER_UUID = "8f7c2a1e-0000-4000-8000-000000000002"
GROUP_ID = "Z3JvdXAtaW50ZXJuYWwtaWQtNDcxMQ=="
IDENTITY = {"number": NUMBER}
PLACEHOLDER = transport.MENTION_PLACEHOLDER


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


class FakeBridge:
    """The bridge endpoints the transport uses, and nothing else."""

    def __init__(self) -> None:
        self.inbox: list[list[dict[str, Any]]] = []
        self.receive_params: list[dict[str, str]] = []
        self.sent: list[dict[str, Any]] = []
        self.accounts = [NUMBER]
        self.mode = "normal"
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if self.refuse:
            return httpx.Response(400, json={"error": "Failed to send message"})
        if path == "/v1/about":
            return httpx.Response(
                200, json={"versions": ["v1", "v2"], "mode": self.mode, "version": "0.0.0"}
            )
        if path == "/v1/accounts":
            return httpx.Response(200, json=self.accounts)
        if path == f"/v1/receive/{NUMBER}":
            self.receive_params.append(dict(request.url.params))
            batch = self.inbox.pop(0) if self.inbox else []
            return httpx.Response(200, json=batch)
        if request.method == "POST" and path == "/v2/send":
            self.sent.append(json.loads(request.content))
            return httpx.Response(201, json={"timestamp": str(int(time.time() * 1000))})
        return httpx.Response(404, json={"error": "not found"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _message(
    text: str,
    *,
    sender: str = CUSTOMER,
    timestamp: int | None = None,
    group: str | None = None,
    mentions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One received item, the way the bridge frames it."""
    sent = timestamp if timestamp is not None else _now_ms()
    data: dict[str, Any] = {"timestamp": sent, "message": text}
    if group is not None:
        data["groupInfo"] = {"groupId": group, "type": "DELIVER"}
    if mentions is not None:
        data["mentions"] = mentions
    envelope = {
        "source": sender,
        "sourceNumber": sender,
        "sourceUuid": CUSTOMER_UUID,
        "sourceName": "Sabine",
        "sourceDevice": 1,
        "timestamp": sent,
        "dataMessage": data,
    }
    return {"envelope": envelope, "account": NUMBER}


def _mention(start: int, number: str = NUMBER) -> dict[str, Any]:
    return {"name": number, "number": number, "uuid": "", "start": start, "length": 1}


def _own(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "own": IDENTITY}


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="signal",
        name="Signal",
        settings_json={"fields": {"base_url": BRIDGE, "number": NUMBER}},
        status="active",
    )
    migrated.add(channel)

    for username, role in (("mohamed", "admin"), ("sabine", "reception"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role=role))
    # The channel's app is installed, as in every workspace that uses the channel.
    await installs.install(migrated, mine.id, "signal")
    await migrated.commit()

    ids = {"channel": channel.id, "workspace": mine.id}
    fake = FakeBridge()
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


async def _lines(db: AsyncSession) -> list[tuple[str, str]]:
    db.expire_all()
    rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    return [(row.speaker, row.text) for row in rows]


# --- The answering policy -----------------------------------------------------


def test_a_direct_message_is_answered_and_the_numbers_own_never_is() -> None:
    assert transport.message_text(_message("Do you open on Saturday?"), IDENTITY) == (
        "Do you open on Saturday?"
    )
    # The number itself - Signal's only automated sender that can be told apart.
    assert transport.message_text(_message("Hello", sender=NUMBER), IDENTITY) is None

    # A copy synced from the owner's phone, a receipt and a typing notice carry no
    # data message.
    synced = _message("sent from my phone")
    synced["envelope"]["syncMessage"] = synced["envelope"].pop("dataMessage")
    assert transport.message_text(synced, IDENTITY) is None
    receipt = _message("")
    receipt["envelope"].pop("dataMessage")
    receipt["envelope"]["receiptMessage"] = {"isRead": True, "timestamps": [1]}
    assert transport.message_text(receipt, IDENTITY) is None

    # A reaction has no text to answer.
    reaction = _message("")
    reaction["envelope"]["dataMessage"]["message"] = None
    reaction["envelope"]["dataMessage"]["reaction"] = {"emoji": "👍"}
    assert transport.message_text(reaction, IDENTITY) is None


def test_a_group_message_is_answered_only_when_mentioned_and_the_mention_is_stripped() -> None:
    assert transport.message_text(_message("anyone around?", group=GROUP_ID), IDENTITY) is None

    addressed = _message(
        f"{PLACEHOLDER} when do you open?", group=GROUP_ID, mentions=[_mention(0)]
    )
    assert transport.message_text(addressed, IDENTITY) == "when do you open?"

    # Mentioning somebody else in the group is not addressing the number.
    other = _message(
        f"{PLACEHOLDER} can you check?", group=GROUP_ID, mentions=[_mention(0, SOMEONE_ELSE)]
    )
    assert transport.message_text(other, IDENTITY) is None


def test_the_mention_is_cut_by_utf16_position_after_an_emoji() -> None:
    """The wave takes two UTF-16 units, so a cut by Python index would miss by one."""
    text = f"👋 {PLACEHOLDER} do you take cards?"
    addressed = _message(text, group=GROUP_ID, mentions=[_mention(3)])
    assert transport.message_text(addressed, IDENTITY) == "👋 do you take cards?"


def test_a_plain_http_bridge_is_accepted_only_on_the_local_network() -> None:
    def address(url: str) -> str:
        return transport.base_url({"base_url": url})

    for local in (
        "http://127.0.0.1:8080",
        "http://localhost:8080/",
        "http://192.168.1.20:8080",
        "http://10.0.0.5",
        "http://[::1]:8080",
        "http://signal-bridge:8080",
        "http://bridge.lan",
        "https://bridge.example.com",
    ):
        assert address(local) == local.rstrip("/")

    for public in ("http://bridge.example.com", "http://8.8.8.8:8080"):
        with pytest.raises(ChannelRefused, match="must use https"):
            address(public)
    with pytest.raises(ChannelRefused, match="http or https"):
        address("ftp://127.0.0.1")
    with pytest.raises(ChannelRefused, match="required"):
        address("")


# --- The receive loop ---------------------------------------------------------


async def test_a_direct_message_becomes_a_conversation_and_the_agent_answers_the_person(
    stage,
) -> None:
    _, ids, fake, db, app = stage
    fake.inbox = [[_message("Do you open on Saturday?")]]

    async with fake.client() as client:
        due = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))
    assert len(due) == 1
    params = fake.receive_params[0]
    assert params["timeout"] == str(transport.RECEIVE_TIMEOUT_SECONDS)
    assert params["send_read_receipts"] == "false"

    await transport.respond(app.state.sessionmaker, ids["channel"], due[0])

    assert fake.sent == [{"message": GREETING, "number": NUMBER, "recipients": [CUSTOMER]}]
    assert await _lines(db) == [("caller", "Do you open on Saturday?"), ("agent", GREETING)]
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None


async def test_a_group_answer_goes_to_the_group(stage) -> None:
    _, ids, fake, db, app = stage
    fake.inbox = [
        [
            _message("morning all", group=GROUP_ID, timestamp=_now_ms() - 1),
            _message(f"{PLACEHOLDER} do you deliver?", group=GROUP_ID, mentions=[_mention(0)]),
        ]
    ]

    async with fake.client() as client:
        due = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))
    assert len(due) == 1
    await transport.respond(app.state.sessionmaker, ids["channel"], due[0])

    group = "group." + base64.b64encode(GROUP_ID.encode()).decode()
    assert fake.sent == [{"message": GREETING, "number": NUMBER, "recipients": [group]}]
    assert await _lines(db) == [("caller", "do you deliver?"), ("agent", GREETING)]


async def test_a_repeated_event_is_dropped_by_its_sender_and_timestamp(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    sent = _now_ms()

    assert (
        await transport.ingest(db, channel, _own(_message("hello", timestamp=sent))) is not None
    )
    assert await transport.ingest(db, channel, _own(_message("hello", timestamp=sent))) is None
    assert await _lines(db) == [("caller", "hello")]


async def test_a_message_a_day_old_is_a_backlog_and_is_not_answered(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    old = _now_ms() - (transport.STALE_SECONDS + 60) * 1000

    assert (
        await transport.ingest(db, channel, _own(_message("last month", timestamp=old))) is None
    )
    assert await _lines(db) == []


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    first = await transport.ingest(db, channel, _own(_message("hello")))
    assert first is not None

    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], first)
    assert fake.sent == []
    later = _own(_message("still there?", timestamp=_now_ms() + 1))
    assert await transport.ingest(db, await _channel_row(db, ids["channel"]), later) is None


async def test_a_pass_rule_hands_the_person_to_a_human(stage) -> None:
    _, ids, fake, db, _ = stage
    db.add(Rule(workspace_id=ids["workspace"], pattern=CUSTOMER, action="pass", note="VIP"))
    await db.commit()
    channel = await _channel_row(db, ids["channel"])

    assert await transport.ingest(db, channel, _own(_message("Call me back."))) is None

    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None and thread.handling == "human"
    tray = (await db.execute(select(Notification))).scalars().all()
    assert [entry.message_key for entry in tray] == ["routed_to_person"]
    assert fake.sent == []


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    due = await transport.ingest(db, channel, _own(_message("hello")))
    assert due is not None

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], due)

    assert await _lines(db) == [("caller", "hello")]


async def test_a_refusing_bridge_fails_the_receive_loudly(stage) -> None:
    """The loop turns this into a health report and a backoff; it must not look like a
    quiet receive with nothing in it."""
    _, ids, fake, db, _ = stage
    fake.refuse = True
    async with fake.client() as client:
        with pytest.raises(ChannelRefused, match="the bridge refused"):
            await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))


# --- The supervisor -------------------------------------------------------------


async def test_the_supervisor_starts_a_connection_and_stops_it_when_the_channel_is_off(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ids, _, db, app = stage
    started: list[int] = []

    async def connection(sessionmaker, channel_id: int) -> None:
        started.append(channel_id)
        await asyncio.Event().wait()

    monkeypatch.setattr(transport, "_connection", connection)
    running: dict[int, tuple[dict[str, str], asyncio.Task]] = {}

    await transport.reconcile(app.state.sessionmaker, running)
    await asyncio.sleep(0)
    assert list(running) == [ids["channel"]]
    assert started == [ids["channel"]]
    task = running[ids["channel"]][1]

    channel = await _channel_row(db, ids["channel"])
    channel.status = "disabled"
    await db.commit()
    await transport.reconcile(app.state.sessionmaker, running)
    await asyncio.sleep(0)

    assert running == {}
    assert task.cancelled() or task.done()


# --- The settings card ----------------------------------------------------------


async def test_the_card_declares_two_shown_fields_and_no_secret(stage) -> None:
    clients, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/signal")).json()

    assert body["setup"]["title"] == "Signal"
    assert [field["name"] for field in body["setup"]["fields"]] == ["base_url", "number"]
    assert [field["secret"] for field in body["setup"]["fields"]] == [False, False]
    assert body["setup"]["verified_live"] is False
    assert body["values"] == {"base_url": BRIDGE, "number": NUMBER}
    assert body["webhook_url"] is None  # Dial-out: no public door.


async def test_the_fields_are_stored_as_typed_and_nothing_is_encrypted(stage) -> None:
    """The issue's secret tests have nothing to hold here; this is what stands in for
    them: what goes in comes back as typed, and nothing lands in the encrypted column."""
    clients, ids, _, db, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/signal", json={"fields": {"base_url": "http://signal-bridge:8080"}}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["values"]["base_url"] == "http://signal-bridge:8080"
    assert (await _channel_row(db, ids["channel"])).credentials_encrypted is None


async def test_removing_the_number_switches_the_channel_off_with_it(stage) -> None:
    clients, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/signal", json={"fields": {"number": ""}}
    )
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    refused = await clients["mohamed"].put("/api/channels/signal", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_public_plain_http_bridge_cannot_be_switched_on(stage) -> None:
    clients, ids, _, db, _ = stage
    answer = await clients["mohamed"].put(
        "/api/channels/signal",
        json={"fields": {"base_url": "http://bridge.example.com"}, "enabled": True},
    )
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "signal_refused"
    assert (await _channel_row(db, ids["channel"])).status == "disabled"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/signal")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/signal", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/signal/test")).status_code == 403


async def test_the_test_button_reports_the_number_and_reports_refusal(stage) -> None:
    clients, _, fake, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/signal/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": NUMBER}

    # A bridge that holds a different number would send as somebody the card does not name.
    fake.accounts = [SOMEONE_ELSE]
    other = await clients["mohamed"].post("/api/channels/signal/test")
    assert other.status_code == 502
    assert other.json()["error"]["code"] == "signal_refused"

    # In JSON-RPC mode the receive endpoint wants a websocket the loop does not speak.
    fake.accounts = [NUMBER]
    fake.mode = "json-rpc"
    rpc = await clients["mohamed"].post("/api/channels/signal/test")
    assert rpc.status_code == 502

    fake.mode = "normal"
    fake.refuse = True
    refused = await clients["mohamed"].post("/api/channels/signal/test")
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "signal_refused"


# --- The takeover reply, delivered ---------------------------------------------


async def test_a_human_reply_reaches_the_chat_the_person_last_wrote_in(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(
        db,
        channel,
        _own(
            _message(f"{PLACEHOLDER} a person, please", group=GROUP_ID, mentions=[_mention(0)])
        ),
    )
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello, Sabine here."}
    )

    assert sent.status_code == 201, sent.text
    group = "group." + base64.b64encode(GROUP_ID.encode()).decode()
    assert fake.sent == [
        {"message": "Hello, Sabine here.", "number": NUMBER, "recipients": [group]}
    ]
