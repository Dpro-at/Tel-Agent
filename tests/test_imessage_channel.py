"""The iMessage channel — the REST bridge on the customer's Mac, and its settings card.

What these tests own is everything the poll loop hands work to: `message_text` (the
answering policy: one-to-one always, groups only when addressed by name, the account's
own messages never), `receive_once` (the cursor, what is asked of the bridge and what
comes back), `ingest` (storage, dedup, backlog, takeover silence), `send_text` (the
private API and its fallback), `respond` (delivery before storage, against a fake
bridge), the supervisor's reconcile pass, the password staying out of every report, and
the generic card's contract for a channel with one secret.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import generic
from api.channels import imessage as transport
from api.channels.generic import ChannelRefused
from api.config import Settings
from api.extensions import installs
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
BRIDGE = "http://192.168.1.20:1234"
BRIDGE_PASSWORD = "bridge-password-7731"  # noqa: S105
ACCOUNT = "front-desk@example.com"
# Numbers from the range reserved for fiction; none of them reaches a person.
CUSTOMER = "+15550100002"
SOMEONE_ELSE = "+15550100003"
DIRECT_CHAT = f"iMessage;-;{CUSTOMER}"
GROUP_CHAT = "iMessage;+;chat471100000000000001"
GROUP_NAME = "Anna"
IDENTITY = {"group_name": GROUP_NAME}


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
def fresh_send_method():
    """What a channel learnt about its bridge's private API does not outlive a test."""
    transport._STANDARD_ONLY.clear()
    yield
    transport._STANDARD_ONLY.clear()


async def _drain() -> None:
    """Let scheduled replies finish so nothing runs mid-flight across test boundaries."""
    pending = list(generic._REPLIES)
    if pending:
        await asyncio.gather(*pending)


class FakeBridge:
    """The bridge endpoints the transport uses, and nothing else."""

    def __init__(self) -> None:
        self.inbox: list[list[dict[str, Any]]] = []
        self.queries: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.account = ACCOUNT
        self.private_api = True
        self.refuse = False

    @staticmethod
    def _answer(status: int, data: Any = None, error: str = "") -> httpx.Response:
        if error:
            return httpx.Response(
                status,
                json={"status": status, "message": error, "error": {"message": error}},
            )
        return httpx.Response(status, json={"status": status, "message": "OK", "data": data})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.params.get("password") != BRIDGE_PASSWORD:
            return self._answer(401, error="You are not authorized to access this resource")
        if self.refuse:
            return self._answer(500, error="Messages is not running")
        path = request.url.path
        if path == "/api/v1/server/info":
            return self._answer(200, {"detected_imessage": self.account, "private_api": True})
        if request.method == "POST" and path == "/api/v1/message/query":
            self.queries.append(json.loads(request.content))
            return self._answer(200, self.inbox.pop(0) if self.inbox else [])
        if request.method == "POST" and path == "/api/v1/message/text":
            body = json.loads(request.content)
            if body.get("method") == "private-api" and not self.private_api:
                return self._answer(500, error="iMessage Private API Helper is not connected")
            self.sent.append(body)
            return self._answer(200, {"guid": "sent-guid"})
        return self._answer(404, error="not found")

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _now_ms() -> int:
    return int(time.time() * 1000)


_GUIDS = iter(range(1, 1_000_000))


def _message(
    text: str,
    *,
    sender: str = CUSTOMER,
    chat: str = DIRECT_CHAT,
    from_me: bool = False,
    sent: int | None = None,
    guid: str | None = None,
) -> dict[str, Any]:
    """One message, the way the bridge's query returns it."""
    return {
        "guid": guid or f"p:0/{next(_GUIDS):08d}",
        "text": text,
        "isFromMe": from_me,
        "dateCreated": sent if sent is not None else _now_ms(),
        "itemType": 0,
        "associatedMessageGuid": None,
        "associatedMessageType": None,
        "handle": {"address": sender},
        "chats": [{"guid": chat, "style": 43 if ";+;" in chat else 45}],
    }


def _own(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "own": IDENTITY}


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="imessage",
        name="iMessage",
        credentials_encrypted=json.dumps({"password": BRIDGE_PASSWORD}),
        settings_json={
            "fields": {"base_url": BRIDGE, "group_name": GROUP_NAME},
            # A position already taken, so a poll asks the bridge for what is new.
            "imessage": {"after": _now_ms() - 60_000},
        },
        status="active",
    )
    migrated.add(channel)

    for username, role in (("mohamed", "admin"), ("sabine", "reception"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role=role))
    # The channel's app is installed, as in every workspace that uses the channel.
    await installs.install(migrated, mine.id, "imessage")
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


def test_a_direct_message_is_answered_and_the_accounts_own_never_is() -> None:
    assert transport.message_text(_message("Do you open on Saturday?"), IDENTITY) == (
        "Do you open on Saturday?"
    )
    # The account itself, from this Mac or the owner's phone - iMessage has no bots, so
    # this is the one automated sender there is to tell apart.
    assert transport.message_text(_message("Hello", from_me=True), IDENTITY) is None

    # A reaction describes itself in its text, and has nothing to answer.
    reaction = _message("Loved “Do you open on Saturday?”")
    reaction["associatedMessageGuid"] = "p:0/00000001"
    reaction["associatedMessageType"] = "love"
    assert transport.message_text(reaction, IDENTITY) is None

    # A group renamed or a person added.
    event = _message("", chat=GROUP_CHAT)
    event["itemType"] = 2
    assert transport.message_text(event, IDENTITY) is None


def test_a_group_message_is_answered_only_when_addressed_and_the_name_is_stripped() -> None:
    def text(words: str, identity: dict[str, str] = IDENTITY) -> str | None:
        return transport.message_text(_message(words, chat=GROUP_CHAT), identity)

    assert text("anyone around?") is None
    assert text("Anna, when do you open?") == "when do you open?"
    assert text("@anna: do you deliver?") == "do you deliver?"
    # A name inside a longer word is not the name.
    assert text("Annabel, are you there?") is None
    # The name alone asks nothing.
    assert text("Anna!") is None
    # With no name on the card, a group is never answered.
    assert text("Anna, when do you open?", {"group_name": ""}) is None


def test_a_plain_http_bridge_is_accepted_only_on_the_local_network() -> None:
    def address(url: str) -> str:
        return transport.base_url({"base_url": url})

    for local in (
        "http://127.0.0.1:1234",
        "http://localhost:1234/",
        "http://192.168.1.20:1234",
        "http://[::1]:1234",
        "http://front-desk-mac:1234",
        "http://front-desk-mac.local:1234",
        "https://bridge.example.com",
    ):
        assert address(local) == local.rstrip("/")

    for public in ("http://bridge.example.com", "http://8.8.8.8:1234"):
        with pytest.raises(ChannelRefused, match="must use https"):
            address(public)
    with pytest.raises(ChannelRefused, match="required"):
        address("")


# --- The poll loop ------------------------------------------------------------


async def test_the_first_poll_takes_the_position_and_answers_no_history(stage) -> None:
    _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    channel.settings_json = {"fields": channel.settings_json["fields"]}
    await db.commit()
    fake.inbox = [[_message("written before the channel was on")]]

    before = _now_ms()
    async with fake.client() as client:
        due = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))

    assert due == []
    assert fake.queries == []
    after = (await _channel_row(db, ids["channel"])).settings_json["imessage"]["after"]
    assert after >= before
    assert await _lines(db) == []


async def test_a_direct_message_becomes_a_conversation_and_the_agent_answers_the_chat(
    stage,
) -> None:
    _, ids, fake, db, app = stage
    newest = _now_ms()
    fake.inbox = [[_message("Do you open on Saturday?", sent=newest)]]

    async with fake.client() as client:
        due = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))
    assert len(due) == 1
    query = fake.queries[0]
    assert query["sort"] == "ASC"
    assert query["with"] == ["chat", "handle"]
    # The position moved to the newest message, and survives in the row.
    stored = (await _channel_row(db, ids["channel"])).settings_json
    assert stored["imessage"]["after"] == newest
    assert stored["fields"] == {"base_url": BRIDGE, "group_name": GROUP_NAME}

    await transport.respond(app.state.sessionmaker, ids["channel"], due[0])

    assert len(fake.sent) == 1
    assert fake.sent[0]["chatGuid"] == DIRECT_CHAT
    assert fake.sent[0]["message"] == GREETING
    assert fake.sent[0]["method"] == "private-api"
    assert await _lines(db) == [("caller", "Do you open on Saturday?"), ("agent", GREETING)]
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None


async def test_the_message_at_the_edge_is_not_read_twice_when_its_thread_is_closed(
    stage,
) -> None:
    """The bridge's `after` includes its own millisecond, so the newest message comes
    back on the next poll. Once its conversation is closed, only the cursor knows it
    was already read."""
    _, ids, fake, db, _ = stage
    edge = _message("Do you open on Saturday?")
    fake.inbox = [[edge], [edge], [edge, _message("And on Sunday?", sent=edge["dateCreated"])]]

    async with fake.client() as client:
        first = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))
        thread = await db.scalar(
            select(Conversation).where(Conversation.external_id == CUSTOMER)
        )
        assert thread is not None
        thread.status = "closed"
        await db.commit()
        again = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))
        # A second message in the same millisecond is new, and is read.
        third = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))

    assert len(first) == 1
    assert again == []
    assert len(third) == 1
    assert fake.queries[1]["after"] == edge["dateCreated"]
    assert await _lines(db) == [
        ("caller", "Do you open on Saturday?"),
        ("caller", "And on Sunday?"),
    ]


async def test_a_group_answer_goes_to_the_group(stage) -> None:
    _, ids, fake, db, app = stage
    fake.inbox = [
        [
            _message("morning all", chat=GROUP_CHAT),
            _message("Anna, do you deliver?", chat=GROUP_CHAT, sender=SOMEONE_ELSE),
        ]
    ]

    async with fake.client() as client:
        due = await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))
    assert len(due) == 1
    await transport.respond(app.state.sessionmaker, ids["channel"], due[0])

    assert [entry["chatGuid"] for entry in fake.sent] == [GROUP_CHAT]
    assert await _lines(db) == [("caller", "do you deliver?"), ("agent", GREETING)]


async def test_without_the_private_api_the_answer_is_sent_the_standard_way_once(
    stage,
) -> None:
    _, ids, fake, db, app = stage
    fake.private_api = False
    channel = await _channel_row(db, ids["channel"])
    due = await transport.ingest(db, channel, _own(_message("hello")))
    assert due is not None

    await transport.respond(app.state.sessionmaker, ids["channel"], due)

    assert [entry["method"] for entry in fake.sent] == ["apple-script"]
    assert fake.sent[0]["tempGuid"].startswith("tel-agent-")
    assert await _lines(db) == [("caller", "hello"), ("agent", GREETING)]


async def test_after_one_refusal_the_private_api_is_not_asked_again_until_fields_change(
    stage,
) -> None:
    clients, ids, fake, db, _ = stage
    fake.private_api = False
    asked: list[str] = []
    original = fake.handler

    def counting(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/message/text":
            asked.append(json.loads(request.content)["method"])
        return original(request)

    fake.handler = counting  # type: ignore[method-assign]
    credentials = generic.credentials_of(await _channel_row(db, ids["channel"]))

    async with fake.client() as client:
        await transport.send_text(client, credentials, DIRECT_CHAT, "one")
        await transport.send_text(client, credentials, DIRECT_CHAT, "two")
    assert asked == ["private-api", "apple-script", "apple-script"]

    # Writing the channel's fields forgets it: the bridge may have its helper now.
    saved = await clients["mohamed"].put(
        "/api/channels/imessage", json={"fields": {"base_url": "http://192.168.1.21:1234"}}
    )
    assert saved.status_code == 200, saved.text
    fake.private_api = True
    async with fake.client() as client:
        await transport.send_text(client, credentials, DIRECT_CHAT, "three")
    assert asked[-1] == "private-api"


async def test_a_repeated_event_is_dropped_by_the_platforms_own_id(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])

    first = _own(_message("hello", guid="p:0/ABCDEF"))
    assert await transport.ingest(db, channel, first) is not None
    assert await transport.ingest(db, channel, first) is None
    assert await _lines(db) == [("caller", "hello")]


async def test_a_message_a_day_old_is_a_backlog_and_is_not_answered(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    old = _now_ms() - (transport.STALE_SECONDS + 60) * 1000

    assert await transport.ingest(db, channel, _own(_message("last month", sent=old))) is None
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
    later = _own(_message("still there?"))
    assert await transport.ingest(db, await _channel_row(db, ids["channel"]), later) is None


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    due = await transport.ingest(db, channel, _own(_message("hello")))
    assert due is not None

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], due)

    assert await _lines(db) == [("caller", "hello")]


async def test_a_refusing_bridge_fails_the_poll_loudly_and_never_names_the_password(
    stage,
) -> None:
    """The loop turns this into a health report and a backoff; it must not look like a
    quiet poll with nothing in it, and what it reports must not carry the password."""
    _, ids, fake, db, _ = stage
    fake.refuse = True
    async with fake.client() as client:
        with pytest.raises(ChannelRefused, match="the bridge refused") as refused:
            await transport.receive_once(db, client, await _channel_row(db, ids["channel"]))
    assert BRIDGE_PASSWORD not in transport._describe(refused.value)

    # A transport failure may quote the URL it failed on; it is reported by type alone.
    unreachable = httpx.ConnectError(f"cannot reach {BRIDGE}/?password={BRIDGE_PASSWORD}")
    assert transport._describe(unreachable) == "ConnectError"


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


async def test_the_card_declares_the_address_the_password_and_the_group_name(stage) -> None:
    clients, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/imessage")).json()

    assert body["setup"]["title"] == "iMessage"
    fields = body["setup"]["fields"]
    assert [field["name"] for field in fields] == ["base_url", "password", "group_name"]
    assert [field["secret"] for field in fields] == [False, True, False]
    assert [field["required"] for field in fields] == [True, True, False]
    assert body["setup"]["verified_live"] is True
    assert body["values"] == {"base_url": BRIDGE, "group_name": GROUP_NAME}
    assert BRIDGE_PASSWORD not in json.dumps(body)
    assert body["webhook_url"] is None  # Dial-out: no public door.


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/imessage", json={"fields": {"password": "fresh-bridge-password-9988"}}
    )
    assert saved.status_code == 200, saved.text
    assert "fresh-bridge-password-9988" not in json.dumps(saved.json())
    assert saved.json()["previews"]["password"].endswith("9988")


async def test_removing_the_secret_switches_the_channel_off_with_it(stage) -> None:
    clients, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/imessage", json={"fields": {"password": ""}}
    )
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    refused = await clients["mohamed"].put("/api/channels/imessage", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_public_plain_http_bridge_cannot_be_switched_on(stage) -> None:
    clients, ids, _, db, _ = stage
    answer = await clients["mohamed"].put(
        "/api/channels/imessage",
        json={"fields": {"base_url": "http://bridge.example.com"}, "enabled": True},
    )
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "imessage_refused"
    assert (await _channel_row(db, ids["channel"])).status == "disabled"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/imessage")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/imessage", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/imessage/test")).status_code == 403


async def test_the_test_button_reports_the_account_and_reports_refusal(stage) -> None:
    clients, _, fake, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/imessage/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": ACCOUNT}

    # A bridge on a Mac where nobody is signed in to Messages sends as nobody.
    fake.account = ""
    signed_out = await clients["mohamed"].post("/api/channels/imessage/test")
    assert signed_out.status_code == 502
    assert signed_out.json()["error"]["code"] == "imessage_refused"

    fake.account = ACCOUNT
    fake.refuse = True
    refused = await clients["mohamed"].post("/api/channels/imessage/test")
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "imessage_refused"
    assert BRIDGE_PASSWORD not in refused.text


# --- The takeover reply, delivered ---------------------------------------------


async def test_a_human_reply_reaches_the_chat_the_person_last_wrote_in(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(
        db, channel, _own(_message("Anna, a person please", chat=GROUP_CHAT))
    )
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello, Sabine here."}
    )

    assert sent.status_code == 201, sent.text
    assert [(entry["chatGuid"], entry["message"]) for entry in fake.sent] == [
        (GROUP_CHAT, "Hello, Sabine here.")
    ]


async def test_the_card_switches_the_channel_off_and_on_again(stage) -> None:
    clients, ids, _, db, _ = stage
    off = await clients["mohamed"].put("/api/channels/imessage", json={"enabled": False})
    assert off.status_code == 200, off.text
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    on = await clients["mohamed"].put("/api/channels/imessage", json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True
    assert (await _channel_row(db, ids["channel"])).status == "active"
