"""The IRC transport — one client connection per channel, on asyncio streams, §B13.

IRC has no platform application and no API: the bot is a nick on a network the customer
chooses, connected over TLS (or plain TCP on a trusted network), speaking the client
protocol directly. Nothing is exposed; this installation dials out.

**One connection per channel, and everything goes through it.** An IRC nick exists only
while its connection is open, so a reply cannot open a second connection the way an HTTP
channel opens a second request - it would arrive as a stranger, or collide with the
bot's own nick. `send_text` therefore writes into the channel's live session, held in
`_SESSIONS`, and refuses when there is none. The test button is the one exception: it
registers under a nick of its own choosing and quits straight away.

**Where the bot answers is a policy.** A private message to the bot is always answered.
In a channel it answers only a line addressed to it the way IRC addresses people -
`nick: question` or `nick, question` - and the address is stripped. A line that merely
mentions the nick mid-sentence is conversation between other people.

**Bots are never customers.** IRC has no bot flag, but it has a convention older than
most platforms' flags: automated senders use NOTICE, and a NOTICE is never answered.
CTCP requests and the bot's own lines are skipped too.

**Lines, not messages.** The protocol caps a line at 512 bytes including the prefix the
server adds, so an answer is cut at `MESSAGE_MAX` *bytes*, between words, and every line
break becomes its own PRIVMSG.

**Message ids where the network has them.** A TCP stream does not redeliver, but a
network that offers IRCv3 `message-tags` gives every line a `msgid`, and a line carrying
one is deduplicated by it like every other channel's events.
"""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import routing
from api.channels import generic
from api.channels.generic import ChannelRefused
from api.channels.setup import Field, Setup
from api.db import session_scope
from api.models import Channel, Conversation

logger = logging.getLogger("api.irc")

KIND = "irc"
INBOUND = "dial_out"

# 512 bytes per line on the wire, minus CRLF, "PRIVMSG <target> :" and the
# ":nick!user@host " prefix the server prepends before relaying it.
MESSAGE_MAX = 400

# How long registration may take before the connection is given up on.
REGISTER_SECONDS = 20.0

# How often the supervisor reconciles running connections against the channels table.
SUPERVISE_SECONDS = 15.0

SETUP = Setup(
    kind=KIND,
    title="IRC",
    note="A nick on the network you choose answers private messages, and channel lines "
    "addressed to it as “nick: question”.",
    guide_url="https://modern.ircdocs.horse/",
    fields=(
        Field(
            "server",
            "Server",
            secret=False,
            help="Host and port. Port 6697 is assumed with TLS, 6667 without.",
            placeholder="irc.example.net:6697",
        ),
        Field(
            "nick",
            "Nick",
            secret=False,
            help="The name the bot uses on the network.",
            placeholder="wagner-support",
        ),
        Field(
            "channels",
            "Channels",
            secret=False,
            required=False,
            help="Channels to join, separated by commas. Leave empty to answer private "
            "messages only.",
            placeholder="#support, #help",
        ),
        Field(
            "password",
            "Password",
            secret=True,
            required=False,
            help="The server or account password, when the network requires one.",
        ),
        Field(
            "tls",
            "TLS",
            secret=False,
            required=False,
            help="“on” unless the server is on your own network and does not offer TLS.",
            placeholder="on",
        ),
    ),
    verified_live=False,
)


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


def make_client() -> httpx.AsyncClient:
    """Required by the channel contract. IRC speaks no HTTP; the client goes unused."""
    return httpx.AsyncClient()


# --- The protocol ---------------------------------------------------------------


@dataclass
class Line:
    """One parsed protocol line."""

    command: str
    params: list[str]
    source: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def nick(self) -> str:
        return self.source.split("!", 1)[0]


def parse(raw: str) -> Line | None:
    """A protocol line, per the message format every IRC server speaks."""
    text = raw.rstrip("\r\n")
    if not text:
        return None
    tags: dict[str, str] = {}
    if text.startswith("@"):
        head, _, text = text.partition(" ")
        for pair in head[1:].split(";"):
            key, _, value = pair.partition("=")
            tags[key] = value
    source = ""
    if text.startswith(":"):
        source, _, text = text[1:].partition(" ")
    trailing = None
    if " :" in text:
        text, _, trailing = text.partition(" :")
    elif text.startswith(":"):
        text, trailing = "", text[1:]
    parts = text.split()
    if not parts:
        return None
    params = parts[1:] + ([trailing] if trailing is not None else [])
    return Line(command=parts[0].upper(), params=params, source=source, tags=tags)


def _settings(credentials: dict[str, str]) -> tuple[str, int, bool]:
    tls = credentials.get("tls", "").strip().lower() not in ("off", "no", "false", "0")
    server = credentials.get("server", "").strip()
    host, _, port = server.rpartition(":") if server.count(":") == 1 else (server, "", "")
    host = host or server
    if not host:
        raise ChannelRefused("server is required")
    try:
        number = int(port) if port else (6697 if tls else 6667)
    except ValueError as error:
        raise ChannelRefused("the server port is not a number") from error
    return host, number, tls


def channels_of(credentials: dict[str, str]) -> list[str]:
    names = re.split(r"[,\s]+", credentials.get("channels", ""))
    return [name if name[0] in "#&" else f"#{name}" for name in names if name]


def split_text(text: str, limit: int = MESSAGE_MAX) -> list[str]:
    """Lines no longer than `limit` *bytes*, cut between words, one per line break."""
    pieces: list[str] = []
    for paragraph in text.splitlines():
        chars = limit
        while True:
            candidate = generic.split_on_words(paragraph, chars)
            if all(len(piece.encode("utf-8")) <= limit for piece in candidate):
                pieces.extend(piece for piece in candidate if piece.strip())
                break
            # Multi-byte text: fewer characters fit. Four bytes is the most one takes.
            chars = max(limit // 4, chars * 3 // 4)
    return pieces


class Session:
    """One registered connection: a reader, a writer, and the nick it holds."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.nick = ""
        self._lock = asyncio.Lock()

    async def send(self, line: str) -> None:
        if any(char in line for char in "\r\n\0"):
            raise ChannelRefused("a protocol line cannot contain a line break")
        async with self._lock:
            self.writer.write(line.encode("utf-8") + b"\r\n")
            await self.writer.drain()

    async def read(self) -> Line | None:
        raw = await self.reader.readline()
        if not raw:
            raise ConnectionError("the server closed the connection")
        return parse(raw.decode("utf-8", errors="replace"))

    async def close(self) -> None:
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass


async def open_session(credentials: dict[str, str], *, nick: str | None = None) -> Session:
    """Connect and register. Returns once the server has welcomed the nick.

    A nick already taken is retried with an underscore, the convention every client
    follows; a refused password or an ERROR line is `ChannelRefused`.
    """
    host, port, tls = _settings(credentials)
    wanted = (nick or credentials.get("nick", "")).strip()
    if not wanted:
        raise ChannelRefused("nick is required")
    context = ssl.create_default_context() if tls else None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context), REGISTER_SECONDS
        )
    except (OSError, TimeoutError, ssl.SSLError) as error:
        raise ChannelRefused(f"could not reach {host}:{port}") from error

    session = Session(reader, writer)
    try:
        await session.send("CAP REQ :message-tags")
        if credentials.get("password"):
            await session.send(f"PASS {credentials['password']}")
        await session.send(f"NICK {wanted}")
        await session.send(f"USER {wanted} 0 * :Tel-Agent")
        await session.send("CAP END")
        attempt = wanted
        async with asyncio.timeout(REGISTER_SECONDS):
            while True:
                line = await session.read()
                if line is None:
                    continue
                if line.command == "PING":
                    await session.send(f"PONG :{line.params[-1] if line.params else ''}")
                elif line.command == "001":
                    session.nick = line.params[0] if line.params else attempt
                    return session
                elif line.command in ("432", "433", "436"):
                    attempt = f"{attempt}_"
                    await session.send(f"NICK {attempt}")
                elif line.command in ("464", "465", "ERROR"):
                    raise ChannelRefused(f"the server refused the connection ({line.command})")
    except BaseException:
        await session.close()
        raise


# Live sessions by channel row id - what `send_text` writes into.
_SESSIONS: dict[int, Session] = {}


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The test button: register under a nick of its own and quit.

    Not the bot's own nick - the running connection holds that, and taking it would
    knock the bot off the network. The network and its welcome are what is proven.
    """
    host, _, _ = _settings(credentials)
    nick = credentials.get("nick", "").strip()
    session = await open_session(credentials, nick=f"{nick[:12]}-test" if nick else None)
    try:
        await session.send("QUIT :connection test")
    finally:
        await session.close()
    return f"{nick} on {host}"


def _address(target: str) -> tuple[str, str]:
    """A reply target is `nick`, or `#channel nick` to address the person in a channel."""
    room, _, person = target.partition(" ")
    return room, person


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """PRIVMSG into this channel's live connection, one line per piece."""
    channel_id = credentials.get(generic.CHANNEL_ID, "")
    session = _SESSIONS.get(int(channel_id)) if channel_id.isdigit() else None
    if session is None:
        raise ChannelRefused("the IRC connection is not open")
    room, person = _address(target)
    prefix = f"{person}: " if person else ""
    for piece in split_text(text, MESSAGE_MAX - len(prefix.encode("utf-8"))):
        await session.send(f"PRIVMSG {room} :{prefix}{piece}")


def message_text(event: Any, identity: Any) -> str | None:
    """The answering policy. `event` is a parsed `Line`; `identity` the bot's nick."""
    if not isinstance(event, Line) or not isinstance(identity, str) or not identity:
        return None
    if event.command != "PRIVMSG" or len(event.params) < 2:
        return None  # NOTICE included: it is how automated senders mark themselves.
    sender = event.nick
    if not sender or sender.lower() == identity.lower():
        return None
    body = event.params[1]
    if body.startswith("\x01"):
        return None  # CTCP: a client asking a client, not a person asking a question.
    body = body.strip()
    if not body:
        return None
    if event.params[0].lower() == identity.lower():
        return body

    address = re.compile(rf"^\s*{re.escape(identity)}\s*[:,](?=\s|$)", re.IGNORECASE)
    if address.match(body) is None:
        return None
    return address.sub("", body, count=1).strip() or None


def reply_target(conversation: Conversation) -> str | None:
    """Where this person last wrote: their nick, or the channel with them addressed."""
    state = (conversation.state_json or {}).get(KIND) or {}
    return str(state.get("target") or "") or None


async def ingest(db: DbSession, channel: Channel, event: Any) -> int | None:
    """Store one addressed line. The stored line's id when a reply is due, else `None`."""
    line, identity = event if isinstance(event, tuple) else (event, None)
    text = message_text(line, identity)
    if text is None:
        return None
    sender = line.nick
    room = line.params[0]
    decision = await routing.decide(
        db, workspace_id=channel.workspace_id, identities=[sender, line.source]
    )
    if decision.action == "block":
        logger.info(
            "irc line dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await generic.conversation_for(db, channel, sender)
    if generic.seen_before(conversation, KIND, line.tags.get("msgid", "")):
        logger.info("irc line repeated, dropped", extra={"conversation_id": conversation.id})
        return None
    private = room.lower() == str(identity).lower()
    state = dict((conversation.state_json or {}).get(KIND) or {})
    state["target"] = sender if private else f"{room} {sender}"
    conversation.state_json = {**(conversation.state_json or {}), KIND: state}
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    stored = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, stored, started)
    if conversation.handling == "human":
        logger.info(
            "irc reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return stored.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The connection ---------------------------------------------------------------


async def _report(
    sessionmaker: async_sessionmaker, channel_id: int, *, ok: bool, detail: str = ""
) -> None:
    from api.channels import health

    async with session_scope(sessionmaker) as db:
        channel = await db.scalar(select(Channel).where(Channel.id == channel_id))
        if channel is None:
            return
        if ok:
            await health.report_ok(db, channel)
        else:
            await health.report_down(db, channel, detail=detail or "connection lost")


async def serve(sessionmaker: async_sessionmaker, channel_id: int, session: Session) -> None:
    """Answer one registered connection until it drops."""
    while True:
        line = await session.read()
        if line is None:
            continue
        if line.command == "PING":
            await session.send(f"PONG :{line.params[-1] if line.params else ''}")
        elif line.command == "NICK" and line.nick.lower() == session.nick.lower():
            session.nick = line.params[0] if line.params else session.nick
        elif line.command == "PRIVMSG":
            async with session_scope(sessionmaker) as db:
                channel = await db.scalar(
                    select(Channel).where(Channel.id == channel_id, Channel.status == "active")
                )
                if channel is None:
                    return
                due = await ingest(db, channel, (line, session.nick))
            if due is not None:
                schedule_reply(sessionmaker, channel_id, due)
        elif line.command == "ERROR":
            raise ConnectionError(line.params[-1] if line.params else "server error")


async def _connection(sessionmaker: async_sessionmaker, channel_id: int) -> None:
    """One channel's connection, forever: backs off from 5 s to 300 s while it fails."""
    backoff = 5.0
    while True:
        session: Session | None = None
        try:
            async with session_scope(sessionmaker) as db:
                channel = await db.scalar(
                    select(Channel).where(Channel.id == channel_id, Channel.status == "active")
                )
                if channel is None:
                    return
                credentials = generic.credentials_of(channel)
            session = await open_session(credentials)
            for name in channels_of(credentials):
                await session.send(f"JOIN {name}")
            _SESSIONS[channel_id] = session
            await _report(sessionmaker, channel_id, ok=True)
            backoff = 5.0
            await serve(sessionmaker, channel_id, session)
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "irc connection dropped",
                extra={"channel_id": channel_id, "error": str(error)[:200]},
            )
            await _report(sessionmaker, channel_id, ok=False, detail=str(error)[:200])
        finally:
            if session is not None:
                if _SESSIONS.get(channel_id) is session:
                    del _SESSIONS[channel_id]
                await session.close()
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300.0)


async def reconcile(
    sessionmaker: async_sessionmaker,
    running: dict[int, tuple[dict[str, str], asyncio.Task]],
) -> None:
    """One supervisor pass: a connection for every active channel, and no other."""
    from api.channels import health

    async with session_scope(sessionmaker) as db:
        rows = await health.usable_channels(db, (KIND,))
        wanted = {row.id: generic.credentials_of(row) for row in rows}

    for channel_id, (credentials, task) in list(running.items()):
        if channel_id not in wanted or wanted[channel_id] != credentials or task.done():
            task.cancel()
            running.pop(channel_id)
    for channel_id, credentials in wanted.items():
        if channel_id not in running:
            running[channel_id] = (
                credentials,
                asyncio.create_task(_connection(sessionmaker, channel_id)),
            )


async def loop(sessionmaker: async_sessionmaker) -> None:
    """The supervisor, started and cancelled by the app lifespan."""
    running: dict[int, tuple[dict[str, str], asyncio.Task]] = {}
    try:
        while True:
            try:
                await reconcile(sessionmaker, running)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("irc supervisor pass failed")
            await asyncio.sleep(SUPERVISE_SECONDS)
    finally:
        for _, task in running.values():
            task.cancel()
