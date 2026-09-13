"""The Matrix channel — a bot on the Client-Server sync, and its settings card.

What these tests own is everything the sync loop hands work to: `message_text` (the
answering policy: direct chats always, rooms only when addressed, bots never),
`sync_once` (position, invites, direct rooms, no answered history), `ingest` (storage,
dedup, rules, takeover silence), `respond` (delivery before storage, against a fake
homeserver), the supervisor's reconcile pass, and the generic card's contract.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import generic
from api.channels import matrix as transport
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
ACCESS_TOKEN = "syt_matrix_bot_token_4711"  # noqa: S105
HOMESERVER = "https://matrix.test"
BOT = "@wagner-bot:matrix.test"
BOT_NAME = "Wagner Support"
CUSTOMER = "@sabine.k:example.org"
DM_ROOM = "!dmroom:matrix.test"
LOBBY = "!lobby:matrix.test"
IDENTITY = {"user_id": BOT, "display_name": BOT_NAME}


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


class FakeHomeserver:
    """The Client-Server API endpoints the transport uses, and nothing else."""

    def __init__(self) -> None:
        self.syncs: list[dict[str, Any]] = []
        self.sync_params: list[dict[str, str]] = []
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.joined: list[str] = []
        self.whoami = BOT
        self.refuse = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if self.refuse or request.headers.get("Authorization") != f"Bearer {ACCESS_TOKEN}":
            return httpx.Response(
                401, json={"errcode": "M_UNKNOWN_TOKEN", "error": "Invalid access token"}
            )
        if path == "/_matrix/client/v3/account/whoami":
            return httpx.Response(200, json={"user_id": self.whoami, "device_id": "DEV"})
        if path.endswith("/displayname"):
            return httpx.Response(200, json={"displayname": BOT_NAME})
        if path == "/_matrix/client/v3/sync":
            self.sync_params.append(dict(request.url.params))
            body = self.syncs.pop(0) if self.syncs else {"next_batch": "s_idle"}
            return httpx.Response(200, json=body)
        if path.startswith("/_matrix/client/v3/join/"):
            room = path.removeprefix("/_matrix/client/v3/join/")
            self.joined.append(room)
            return httpx.Response(200, json={"room_id": room})
        if request.method == "PUT" and "/send/m.room.message/" in path:
            room = path.split("/rooms/", 1)[1].split("/send/", 1)[0]
            self.sent.append((room, json.loads(request.content)))
            return httpx.Response(200, json={"event_id": f"$sent{len(self.sent)}"})
        return httpx.Response(404, json={"errcode": "M_UNRECOGNIZED"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _message(
    body: str,
    *,
    event_id: str = "$e1",
    sender: str = CUSTOMER,
    msgtype: str = "m.text",
    mentions: list[str] | None = None,
    event_type: str = "m.room.message",
) -> dict[str, Any]:
    content: dict[str, Any] = {"msgtype": msgtype, "body": body}
    if mentions is not None:
        content["m.mentions"] = {"user_ids": mentions}
    return {"type": event_type, "event_id": event_id, "sender": sender, "content": content}


def _wrapped(raw: dict[str, Any], *, room: str = DM_ROOM, direct: bool = True) -> dict:
    return {"room_id": room, "direct": direct, "event": raw, "own": IDENTITY}


def _sync(
    next_batch: str,
    *,
    join: dict[str, list[dict[str, Any]]] | None = None,
    members: dict[str, int] | None = None,
    invite: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rooms: dict[str, Any] = {}
    if join:
        rooms["join"] = {
            room: {
                "timeline": {"events": events},
                **(
                    {"summary": {"m.joined_member_count": members[room]}}
                    if members and room in members
                    else {}
                ),
            }
            for room, events in join.items()
        }
    if invite:
        rooms["invite"] = invite
    return {"next_batch": next_batch, "rooms": rooms}


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="matrix",
        name="Matrix",
        credentials_encrypted=json.dumps({"access_token": ACCESS_TOKEN}),
        settings_json={"fields": {"homeserver_url": HOMESERVER, "user_id": BOT}},
        status="active",
    )
    migrated.add(channel)

    for username, role in (("mohamed", "admin"), ("sabine", "reception"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role=role))
    # The channel's app is installed, as in every workspace that uses the channel.
    await installs.install(migrated, mine.id, "matrix")
    await migrated.commit()

    ids = {"channel": channel.id, "workspace": mine.id}
    fake = FakeHomeserver()
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


def test_a_direct_message_is_answered_and_a_bot_never_is() -> None:
    assert transport.message_text(_wrapped(_message("Do you open on Saturday?")), IDENTITY) == (
        "Do you open on Saturday?"
    )
    # The bot itself.
    assert transport.message_text(_wrapped(_message("Hello", sender=BOT)), IDENTITY) is None
    # `m.notice` is how an automated sender marks itself.
    assert (
        transport.message_text(_wrapped(_message("Reminder", msgtype="m.notice")), IDENTITY)
        is None
    )
    # An encrypted event is skipped rather than half-understood.
    encrypted = _message("", event_type="m.room.encrypted")
    assert transport.message_text(_wrapped(encrypted), IDENTITY) is None
    # An edit is not a new question.
    edit = _message("* Do you open on Sunday?")
    edit["content"]["m.relates_to"] = {"rel_type": "m.replace", "event_id": "$e0"}
    assert transport.message_text(_wrapped(edit), IDENTITY) is None


def test_a_room_message_is_answered_only_when_addressed_and_the_address_is_stripped() -> None:
    def room(raw: dict[str, Any]) -> dict:
        return _wrapped(raw, room=LOBBY, direct=False)

    assert transport.message_text(room(_message("anyone around?")), IDENTITY) is None
    # A word that happens to be the display name, mid-sentence, is not an address.
    assert (
        transport.message_text(room(_message("we need wagner support tomorrow")), IDENTITY)
        is None
    )

    by_pill = _message(f"{BOT_NAME}: when do you open?", mentions=[BOT])
    assert transport.message_text(room(by_pill), IDENTITY) == "when do you open?"

    by_id = _message(f"{BOT}: is parking free?")
    assert transport.message_text(room(by_id), IDENTITY) == "is parking free?"

    by_localpart = _message("wagner-bot, can I bring a dog?")
    assert transport.message_text(room(by_localpart), IDENTITY) == "can I bring a dog?"

    # A lookalike id is somebody else.
    lookalike = _message("@wagner-bot:matrix.test.evil: hello")
    assert transport.message_text(room(lookalike), IDENTITY) is None

    # `m.mentions` alone is enough, whatever the text says.
    mentioned = _message("see above", mentions=[BOT])
    assert transport.message_text(room(mentioned), IDENTITY) == "see above"


# --- The sync -----------------------------------------------------------------


async def test_the_first_sync_takes_the_position_and_answers_no_history(stage) -> None:
    _, ids, fake, db, _ = stage
    fake.syncs = [_sync("s1", join={DM_ROOM: [_message("an old question")]})]
    channel = await _channel_row(db, ids["channel"])

    async with fake.client() as client:
        due = await transport.sync_once(db, client, channel, IDENTITY)

    assert due == []
    assert await _lines(db) == []
    assert fake.sync_params[0]["timeout"] == "0"
    assert "since" not in fake.sync_params[0]
    assert (await _channel_row(db, ids["channel"])).settings_json["matrix"][
        "next_batch"
    ] == "s1"


async def test_a_direct_message_becomes_a_conversation_and_the_agent_answers_into_the_room(
    stage,
) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    channel.settings_json = {**channel.settings_json, "matrix": {"next_batch": "s1"}}
    await db.commit()
    fake.syncs = [
        _sync(
            "s2", join={DM_ROOM: [_message("Do you open on Saturday?")]}, members={DM_ROOM: 2}
        )
    ]

    async with fake.client() as client:
        due = await transport.sync_once(
            db, client, await _channel_row(db, ids["channel"]), IDENTITY
        )
    assert len(due) == 1
    assert fake.sync_params[0]["since"] == "s1"

    await transport.respond(app.state.sessionmaker, ids["channel"], due[0])

    assert fake.sent == [(DM_ROOM, {"msgtype": "m.text", "body": GREETING})]
    assert await _lines(db) == [("caller", "Do you open on Saturday?"), ("agent", GREETING)]
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    stored = (await _channel_row(db, ids["channel"])).settings_json["matrix"]
    assert stored["next_batch"] == "s2"


async def test_an_invite_is_accepted_and_a_direct_invite_marks_the_room(stage) -> None:
    _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    channel.settings_json = {**channel.settings_json, "matrix": {"next_batch": "s1"}}
    await db.commit()
    invite_state = {
        "invite_state": {
            "events": [
                {
                    "type": "m.room.member",
                    "state_key": BOT,
                    "sender": CUSTOMER,
                    "content": {"membership": "invite", "is_direct": True},
                }
            ]
        }
    }
    fake.syncs = [
        _sync("s2", invite={DM_ROOM: invite_state}),
        # No member count this time: the invite is what says the room is direct.
        _sync("s3", join={DM_ROOM: [_message("hi, a quick question")]}),
    ]

    async with fake.client() as client:
        assert (
            await transport.sync_once(
                db, client, await _channel_row(db, ids["channel"]), IDENTITY
            )
            == []
        )
        assert fake.joined == [DM_ROOM]
        due = await transport.sync_once(
            db, client, await _channel_row(db, ids["channel"]), IDENTITY
        )

    assert len(due) == 1
    assert await _lines(db) == [("caller", "hi, a quick question")]


async def test_a_busy_room_is_not_answered_unless_the_bot_is_addressed(stage) -> None:
    _, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    channel.settings_json = {**channel.settings_json, "matrix": {"next_batch": "s1"}}
    await db.commit()
    fake.syncs = [
        _sync(
            "s2",
            join={
                LOBBY: [
                    _message("morning all", event_id="$a"),
                    _message(f"{BOT_NAME}: do you take cards?", event_id="$b", mentions=[BOT]),
                ]
            },
            members={LOBBY: 5},
        )
    ]

    async with fake.client() as client:
        due = await transport.sync_once(
            db, client, await _channel_row(db, ids["channel"]), IDENTITY
        )

    assert len(due) == 1
    assert await _lines(db) == [("caller", "do you take cards?")]


async def test_a_repeated_event_is_dropped_by_its_event_id(stage) -> None:
    _, ids, _, db, _ = stage
    channel = await _channel_row(db, ids["channel"])

    assert (
        await transport.ingest(db, channel, _wrapped(_message("hello", event_id="$dup")))
        is not None
    )
    assert (
        await transport.ingest(db, channel, _wrapped(_message("hello", event_id="$dup")))
        is None
    )
    assert await _lines(db) == [("caller", "hello")]


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    first = await transport.ingest(db, channel, _wrapped(_message("hello", event_id="$1")))
    assert first is not None

    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], first)
    assert fake.sent == []
    later = _wrapped(_message("still there?", event_id="$2"))
    assert await transport.ingest(db, await _channel_row(db, ids["channel"]), later) is None


async def test_a_pass_rule_hands_the_person_to_a_human(stage) -> None:
    _, ids, fake, db, _ = stage
    db.add(Rule(workspace_id=ids["workspace"], pattern=CUSTOMER, action="pass", note="VIP"))
    await db.commit()
    channel = await _channel_row(db, ids["channel"])

    assert await transport.ingest(db, channel, _wrapped(_message("Call me back."))) is None

    db.expire_all()
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None and thread.handling == "human"
    tray = (await db.execute(select(Notification))).scalars().all()
    assert [entry.message_key for entry in tray] == ["routed_to_person"]
    assert fake.sent == []


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    _, ids, fake, db, app = stage
    channel = await _channel_row(db, ids["channel"])
    due = await transport.ingest(db, channel, _wrapped(_message("hello")))
    assert due is not None

    fake.refuse = True
    await transport.respond(app.state.sessionmaker, ids["channel"], due)

    assert await _lines(db) == [("caller", "hello")]


async def test_a_rejected_token_fails_the_sync_loudly(stage) -> None:
    """The loop turns this into a health report and a backoff; it must not look like a
    quiet sync with nothing in it."""
    _, ids, fake, db, _ = stage
    fake.refuse = True
    async with fake.client() as client:
        with pytest.raises(ChannelRefused, match="M_UNKNOWN_TOKEN"):
            await transport.sync_once(
                db, client, await _channel_row(db, ids["channel"]), IDENTITY
            )


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


async def test_the_card_declares_its_fields_and_says_what_it_cannot_read(stage) -> None:
    clients, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/matrix")).json()

    assert body["setup"]["title"] == "Matrix"
    assert [field["name"] for field in body["setup"]["fields"]] == [
        "homeserver_url",
        "user_id",
        "access_token",
    ]
    assert [field["secret"] for field in body["setup"]["fields"]] == [False, False, True]
    assert "Unencrypted rooms only" in body["setup"]["note"]
    assert body["values"]["homeserver_url"] == HOMESERVER
    assert body["previews"]["access_token"].endswith("4711")
    assert body["webhook_url"] is None  # Dial-out: no public door.


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/matrix", json={"fields": {"access_token": "syt_fresh_token_9988"}}
    )
    assert saved.status_code == 200, saved.text
    assert "syt_fresh_token_9988" not in json.dumps(saved.json())
    assert saved.json()["previews"]["access_token"].endswith("9988")


async def test_removing_the_token_switches_the_channel_off_with_it(stage) -> None:
    clients, ids, _, db, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/matrix", json={"fields": {"access_token": ""}}
    )
    assert cleared.json()["previews"]["access_token"] is None
    assert cleared.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"

    refused = await clients["mohamed"].put("/api/channels/matrix", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/matrix")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/matrix", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/matrix/test")).status_code == 403


async def test_the_test_button_reports_the_bot_and_reports_refusal(stage) -> None:
    clients, _, fake, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/matrix/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": BOT}

    # A token that belongs to a different account is refused, not believed.
    fake.whoami = "@someone-else:matrix.test"
    other = await clients["mohamed"].post("/api/channels/matrix/test")
    assert other.status_code == 502
    assert other.json()["error"]["code"] == "matrix_refused"

    fake.whoami = BOT
    fake.refuse = True
    refused = await clients["mohamed"].post("/api/channels/matrix/test")
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "matrix_refused"


# --- The takeover reply, delivered ---------------------------------------------


async def test_a_human_reply_reaches_the_room_the_person_last_wrote_in(stage) -> None:
    clients, ids, fake, db, _ = stage
    channel = await _channel_row(db, ids["channel"])
    await transport.ingest(db, channel, _wrapped(_message("a person, please")))
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == CUSTOMER))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello, Sabine here."}
    )

    assert sent.status_code == 201, sent.text
    assert fake.sent == [(DM_ROOM, {"msgtype": "m.text", "body": "Hello, Sabine here."})]
