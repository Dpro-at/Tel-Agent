"""The IRC channel — one client connection on asyncio streams, and its settings card.

The fake platform here is a real TCP server on localhost speaking the part of the client
protocol the transport uses: registration, PING, JOIN, PRIVMSG. The connection under
test is the real one, so what these tests prove is the whole path - a line arrives on a
socket, is stored, answered by the shared answer path and written back to the socket.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import generic, health
from api.channels import irc as transport
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
NICK = "wagner-support"
SERVER_PASSWORD = "network-pass-4711"  # noqa: S105


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


async def _until(check: Callable[[], bool], seconds: float = 5.0) -> None:
    """Wait for something another task does. Polled: the fake has no event to wait on."""
    async with asyncio.timeout(seconds):
        while not check():  # noqa: ASYNC110
            await asyncio.sleep(0.02)


class FakeNetwork:
    """A localhost IRC server: welcomes, answers PING, and records every line."""

    def __init__(self) -> None:
        self.received: list[str] = []
        self.clients: list[asyncio.StreamWriter] = []
        self.taken: set[str] = set()
        self.password: str | None = None
        self.port = 0
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in self.clients:
            writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients.append(writer)
        given_password = None
        try:
            while raw := await reader.readline():
                line = raw.decode().rstrip("\r\n")
                self.received.append(line)
                command, _, rest = line.partition(" ")
                if command == "PASS":
                    given_password = rest
                elif command == "NICK":
                    if self.password is not None and given_password != self.password:
                        writer.write(b":fake.test 464 * :Password incorrect\r\n")
                    elif rest in self.taken:
                        writer.write(
                            f":fake.test 433 * {rest} :Nickname is in use\r\n".encode()
                        )
                    else:
                        writer.write(f":fake.test 001 {rest} :Welcome\r\n".encode())
                    await writer.drain()
        except (ConnectionError, OSError):
            pass

    async def push(self, line: str) -> None:
        """A line from the network to every connected client."""
        for writer in self.clients:
            if not writer.is_closing():
                writer.write(line.encode() + b"\r\n")
                await writer.drain()

    def said(self, prefix: str) -> list[str]:
        return [line for line in self.received if line.startswith(prefix)]


@pytest.fixture
async def network():
    fake = FakeNetwork()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def stage(
    migrated: AsyncSession, settings: Settings, database_url: str, network: FakeNetwork
):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="irc",
        name="IRC",
        credentials_encrypted=json.dumps({"password": SERVER_PASSWORD}),
        settings_json={
            "fields": {
                "server": f"127.0.0.1:{network.port}",
                "nick": NICK,
                "channels": "#support",
                "tls": "off",
            }
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
    await installs.install(migrated, mine.id, "irc")
    await migrated.commit()

    ids = {"channel": channel.id, "workspace": mine.id}
    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    connections: list[asyncio.Task] = []
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
            yield clients, ids, network, migrated, app, connections
        finally:
            for task in connections:
                task.cancel()
            await asyncio.gather(*connections, return_exceptions=True)
            pending = list(generic._REPLIES)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for http in clients.values():
                await http.aclose()


async def _connect(stage) -> None:
    """Start the channel's real connection and wait until it has joined."""
    _, ids, network, _, app, connections = stage
    connections.append(
        asyncio.create_task(transport._connection(app.state.sessionmaker, ids["channel"]))
    )
    await _until(lambda: bool(network.said("JOIN #support")))
    await _until(lambda: ids["channel"] in transport._SESSIONS)


async def _lines(db: AsyncSession) -> list[tuple[str, str]]:
    db.expire_all()
    rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    return [(row.speaker, row.text) for row in rows]


async def _channel_row(db: AsyncSession, channel_id: int) -> Channel:
    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.id == channel_id))
    assert row is not None
    return row


# --- The protocol and the answering policy ---------------------------------------


def test_a_line_parses_with_tags_source_and_trailing_text() -> None:
    line = transport.parse("@msgid=abc;time=x :sabine!s@host PRIVMSG #support :hi: there\r\n")
    assert line is not None
    assert (line.command, line.nick, line.params) == (
        "PRIVMSG",
        "sabine",
        ["#support", "hi: there"],
    )
    assert line.tags["msgid"] == "abc"
    assert transport.parse("PING :fake.test").params == ["fake.test"]


def test_a_private_message_is_answered_and_a_bot_never_is() -> None:
    def said(raw: str) -> str | None:
        return transport.message_text(transport.parse(raw), NICK)

    assert said(f":sabine!s@h PRIVMSG {NICK} :Do you open on Saturday?") == (
        "Do you open on Saturday?"
    )
    # NOTICE is how automated senders mark themselves.
    assert said(f":helpbot!b@h NOTICE {NICK} :You have 1 memo") is None
    # CTCP is a client asking a client.
    assert said(f":sabine!s@h PRIVMSG {NICK} :\x01VERSION\x01") is None
    # The bot's own line, echoed.
    assert said(f":{NICK}!b@h PRIVMSG #support :hello") is None


def test_a_channel_line_is_answered_only_when_addressed_and_the_address_is_stripped() -> None:
    def said(body: str) -> str | None:
        return transport.message_text(
            transport.parse(f":sabine!s@h PRIVMSG #support :{body}"), NICK
        )

    assert said("anyone around?") is None
    assert said(f"ask {NICK} about parking") is None  # a mention is not an address
    assert said(f"{NICK}2: hello") is None  # somebody else
    assert said(f"{NICK}: is parking free?") == "is parking free?"
    assert said(f"{NICK.upper()}, can I bring a dog?") == "can I bring a dog?"


def test_an_answer_is_cut_into_lines_that_fit_in_bytes() -> None:
    assert transport.split_text("one\ntwo") == ["one", "two"]
    arabic = "مرحبا " * 200
    pieces = transport.split_text(arabic.strip(), 400)
    assert len(pieces) > 1
    assert all(len(piece.encode("utf-8")) <= 400 for piece in pieces)
    assert " ".join(pieces) == arabic.strip()


# --- The connection, end to end ------------------------------------------------------


async def test_a_private_message_becomes_a_conversation_and_the_agent_answers_it(stage) -> None:
    _, ids, network, db, _, _ = stage
    await _connect(stage)
    assert network.said(f"PASS {SERVER_PASSWORD}")
    await _until(lambda: ("irc", ids["channel"]) in health.snapshot())
    assert health.snapshot()[("irc", ids["channel"])].state == "ok"

    await network.push(f":sabine!s@example.org PRIVMSG {NICK} :Do you open on Saturday?")
    await _until(lambda: bool(network.said("PRIVMSG sabine :")))

    assert network.said("PRIVMSG sabine :") == [f"PRIVMSG sabine :{GREETING}"]
    pending = list(generic._REPLIES)
    if pending:
        await asyncio.gather(*pending)
    assert await _lines(db) == [("caller", "Do you open on Saturday?"), ("agent", GREETING)]


async def test_an_addressed_channel_line_is_answered_in_the_channel_to_that_person(
    stage,
) -> None:
    _, _, network, db, _, _ = stage
    await _connect(stage)

    await network.push(":sabine!s@example.org PRIVMSG #support :morning all")
    await network.push(f":sabine!s@example.org PRIVMSG #support :{NICK}: is parking free?")
    await _until(lambda: bool(network.said("PRIVMSG #support :")))

    assert network.said("PRIVMSG #support :") == [f"PRIVMSG #support :sabine: {GREETING}"]
    pending = list(generic._REPLIES)
    if pending:
        await asyncio.gather(*pending)
    assert (await _lines(db))[0] == ("caller", "is parking free?")


async def test_the_connection_answers_ping(stage) -> None:
    _, _, network, _, _, _ = stage
    await _connect(stage)
    await network.push("PING :keepalive-42")
    await _until(lambda: bool(network.said("PONG :keepalive-42")))


async def test_a_taken_nick_is_retried_with_an_underscore(network: FakeNetwork) -> None:
    network.taken.add(NICK)
    session = await transport.open_session(
        {"server": f"127.0.0.1:{network.port}", "nick": NICK, "tls": "off"}
    )
    try:
        assert session.nick == f"{NICK}_"
    finally:
        await session.close()


async def test_a_refused_password_is_a_refusal(network: FakeNetwork) -> None:
    network.password = SERVER_PASSWORD
    with pytest.raises(ChannelRefused):
        await transport.open_session(
            {"server": f"127.0.0.1:{network.port}", "nick": NICK, "tls": "off", "password": "x"}
        )


# --- Storage -------------------------------------------------------------------------


def _event(body: str, *, msgid: str = "", target: str = NICK) -> tuple:
    tags = f"@msgid={msgid} " if msgid else ""
    return (transport.parse(f"{tags}:sabine!s@example.org PRIVMSG {target} :{body}"), NICK)


async def test_a_repeated_line_is_dropped_by_its_message_id(stage) -> None:
    _, ids, _, db, _, _ = stage
    channel = await _channel_row(db, ids["channel"])
    assert await transport.ingest(db, channel, _event("hello", msgid="m1")) is not None
    assert await transport.ingest(db, channel, _event("hello", msgid="m1")) is None
    assert await _lines(db) == [("caller", "hello")]


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    _, ids, network, db, app, _ = stage
    channel = await _channel_row(db, ids["channel"])
    first = await transport.ingest(db, channel, _event("hello"))
    assert first is not None
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "sabine"))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    await transport.respond(app.state.sessionmaker, ids["channel"], first)

    assert network.said("PRIVMSG") == []
    assert (
        await transport.ingest(db, await _channel_row(db, ids["channel"]), _event("hi?"))
        is None
    )


async def test_a_pass_rule_hands_the_person_to_a_human(stage) -> None:
    _, ids, _, db, _, _ = stage
    db.add(Rule(workspace_id=ids["workspace"], pattern="sabine", action="pass", note="VIP"))
    await db.commit()

    assert (
        await transport.ingest(db, await _channel_row(db, ids["channel"]), _event("hi")) is None
    )

    db.expire_all()
    tray = (await db.execute(select(Notification))).scalars().all()
    assert [entry.message_key for entry in tray] == ["routed_to_person"]


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    """No connection open: the send is refused, and nothing claims an answer went out."""
    _, ids, _, db, app, _ = stage
    due = await transport.ingest(db, await _channel_row(db, ids["channel"]), _event("hello"))
    assert due is not None

    await transport.respond(app.state.sessionmaker, ids["channel"], due)

    assert await _lines(db) == [("caller", "hello")]


# --- The supervisor ----------------------------------------------------------------


async def test_the_supervisor_starts_a_connection_and_stops_it_when_the_channel_is_off(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, ids, _, db, app, _ = stage
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


# --- The settings card -----------------------------------------------------------------


async def test_the_card_declares_its_fields(stage) -> None:
    clients, _, _, _, _, _ = stage
    body = (await clients["mohamed"].get("/api/channels/irc")).json()

    assert body["setup"]["title"] == "IRC"
    fields = {field["name"]: field for field in body["setup"]["fields"]}
    assert list(fields) == ["server", "nick", "channels", "password", "tls"]
    assert [name for name, field in fields.items() if field["secret"]] == ["password"]
    assert [name for name, field in fields.items() if field["required"]] == ["server", "nick"]
    assert body["values"]["nick"] == NICK
    assert body["previews"]["password"].endswith("4711")
    assert body["webhook_url"] is None


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/irc", json={"fields": {"password": "fresh-network-pass-9988"}}
    )
    assert saved.status_code == 200, saved.text
    assert "fresh-network-pass-9988" not in json.dumps(saved.json())
    assert saved.json()["previews"]["password"].endswith("9988")


async def test_removing_a_required_field_switches_the_channel_off_and_the_optional_one_does_not(
    stage,
) -> None:
    """The password is optional on IRC - many networks have none - so clearing it leaves
    the channel on. Clearing the nick takes it down."""
    clients, ids, _, db, _, _ = stage
    cleared = await clients["mohamed"].put(
        "/api/channels/irc", json={"fields": {"password": ""}}
    )
    assert cleared.json()["previews"]["password"] is None
    assert cleared.json()["enabled"] is True

    no_nick = await clients["mohamed"].put("/api/channels/irc", json={"fields": {"nick": ""}})
    assert no_nick.json()["enabled"] is False
    assert (await _channel_row(db, ids["channel"])).status == "disabled"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/irc")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/irc", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/irc/test")).status_code == 403


async def test_the_test_button_reports_the_network_and_reports_refusal(stage) -> None:
    clients, _, network, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/irc/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": f"{NICK} on 127.0.0.1"}
    # Under a nick of its own, so a running bot is not knocked off the network.
    assert network.said("NICK wagner-suppo-test")

    network.password = "a-different-password"  # noqa: S105
    refused = await clients["mohamed"].post("/api/channels/irc/test")
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "irc_refused"


# --- The takeover reply, delivered ------------------------------------------------------


async def test_a_human_reply_goes_out_on_the_live_connection(stage) -> None:
    clients, ids, network, db, _, _ = stage
    await _connect(stage)
    await transport.ingest(
        db, await _channel_row(db, ids["channel"]), _event("a person, please")
    )
    thread = await db.scalar(select(Conversation).where(Conversation.external_id == "sabine"))
    assert thread is not None
    thread.handling = "human"
    await db.commit()

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello, Sabine here."}
    )

    assert sent.status_code == 201, sent.text
    await _until(lambda: bool(network.said("PRIVMSG sabine :")))
    assert network.said("PRIVMSG sabine :") == ["PRIVMSG sabine :Hello, Sabine here."]
