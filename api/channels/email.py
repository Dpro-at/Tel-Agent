"""The email transport — §B13's third no-platform channel.

An IMAP/SMTP mailbox the customer already owns: no developer account, no review
queue, nothing to be approved by. The mailbox credentials are the channel's own —
per workspace, entered on the Channels card, the password in the encrypted column —
and they are **not** the installation's notification SMTP from the settings store:
that one is how Tel-Agent talks to its operator, this one is how a business talks to
its customers, and §B9.2's whole point is that those are different credentials with
different owners.

**The conversation is keyed by the correspondent, not by the mail thread.** §B5 keys
a thread by what the channel uses to name the other end — a caller's number, a chat
id — and on email that is the address: a customer who writes three mails with three
subjects is one person mid-conversation, exactly as they would be on the phone. The
mail-protocol threading (In-Reply-To / References) still rides along, kept in
`state_json`, so what lands in the customer's inbox threads properly in *their*
client. The address is an identity like a number, so unlike the web handle it may be
shown.

**The unseen flag is the offset.** Fetching a message's body sets `\\Seen` on the
server, so what has been ingested is recorded where the mailbox is - a crash loses
nothing and redelivers nothing, the same property Telegram's confirmed offset gives.
The mailbox stays usable by a person alongside: mail they have read is mail this
loop will not answer, which is also the honest behaviour for a shared inbox.

**Loops are the failure this channel must not have.** An agent that answers an
out-of-office answers it forever. Anything stamped `Auto-Submitted` (RFC 3834) or
`Precedence: bulk/junk` is stored for the record and never answered, and mail from
the channel's own address is skipped entirely.
"""

from __future__ import annotations

import asyncio
import contextlib
import email as email_stdlib
import email.policy
import imaplib
import logging
import re
import smtplib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent.config import ConfigurationError
from agent.reply import reply as generate_reply
from agent.tools import TakenMessage
from api import llm, webhooks
from api.conversations import position_ms
from api.db import session_scope
from api.models import Channel, Conversation, Message
from api.notifications import raise_notification

logger = logging.getLogger("api.email")

# Between passes over the inboxes. A minute, not seconds: email is the one channel
# whose own culture says the user waits, and an IMAP dance per channel per beat is
# paid against somebody else's mail server.
POLL_SECONDS = 60.0

# One mail must not become a novel in the transcript, nor a novel in the model's
# prompt. Far above any real customer mail; a newsletter that slips past the
# auto-submitted check is cut here.
BODY_MAX = 8000


class EmailError(Exception):
    """The mailbox said no — connection, login, or a refused send."""


@dataclass(frozen=True)
class MailboxConfig:
    """One channel's mailbox, assembled from its row."""

    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    from_address: str
    imap_ssl: bool
    smtp_tls: bool
    smtp_ssl: bool


def config_for(channel: Channel) -> MailboxConfig | None:
    """The mailbox this channel polls, or None while the card is incomplete."""
    settings = channel.settings_json or {}
    imap_host = settings.get("imap_host")
    smtp_host = settings.get("smtp_host")
    username = settings.get("username")
    if not (imap_host and smtp_host and username and channel.credentials_encrypted):
        return None
    return MailboxConfig(
        imap_host=str(imap_host),
        imap_port=int(settings.get("imap_port") or 993),
        smtp_host=str(smtp_host),
        smtp_port=int(settings.get("smtp_port") or 587),
        username=str(username),
        password=channel.credentials_encrypted,
        from_address=str(settings.get("from_address") or username),
        imap_ssl=bool(settings.get("imap_ssl", True)),
        smtp_tls=bool(settings.get("smtp_tls", True)),
        smtp_ssl=bool(settings.get("smtp_ssl", False)),
    )


@dataclass(frozen=True)
class Inbound:
    """One arrived mail, reduced to what a conversation stores."""

    sender: str
    subject: str
    text: str
    message_id: str
    # RFC 3834 / Precedence: an autoresponder's mail. Stored, never answered.
    auto_submitted: bool


# --- The wire (blocking; every caller hands these to a worker thread) ----------


def _imap_connect(config: MailboxConfig) -> imaplib.IMAP4:
    if config.imap_ssl:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
    else:
        client = imaplib.IMAP4(config.imap_host, config.imap_port)
    client.login(config.username, config.password)
    return client


def _text_of(message: email_stdlib.message.Message) -> str:
    """The text a person wrote, out of whatever their client wrapped it in.

    `text/plain` when there is one — every mainstream client sends it alongside HTML.
    A rare HTML-only mail is stripped of tags rather than skipped: badly rendered
    beats silently ignored, for a message a customer is waiting on an answer to.
    """
    body = message.get_body(preferencelist=("plain",))  # type: ignore[union-attr]
    if body is not None:
        return str(body.get_content())
    html = message.get_body(preferencelist=("html",))  # type: ignore[union-attr]
    if html is None:
        return ""
    stripped = re.sub(r"<[^>]+>", " ", str(html.get_content()))
    return re.sub(r"\s+", " ", stripped).strip()


_QUOTE_INTRO = re.compile(r"^On .{0,200} wrote:\s*$")


def strip_quotes(text: str) -> str:
    """The new words, without the conversation they were pasted on top of.

    Reply-quoting would otherwise store the whole thread again in every message and
    hand the model its own history twice. Two conventions cover the mainstream
    clients: lines starting `>` and the `On … wrote:` line that introduces them.
    Anything unusual survives untouched — over-trimming loses a customer's words,
    under-trimming only repeats them.
    """
    kept: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue
        if _QUOTE_INTRO.match(line.strip()):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _parse(raw: bytes) -> Inbound | None:
    message = email_stdlib.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(message.get("From") or ""))[1].strip().lower()
    if not sender:
        return None
    auto = bool(
        (str(message.get("Auto-Submitted") or "no").lower() not in ("", "no"))
        or str(message.get("Precedence") or "").lower() in ("bulk", "junk", "list")
    )
    text = strip_quotes(_text_of(message))[:BODY_MAX]
    return Inbound(
        sender=sender,
        subject=str(message.get("Subject") or "").strip(),
        text=text.strip(),
        message_id=str(message.get("Message-ID") or "").strip(),
        auto_submitted=auto,
    )


def _fetch_unseen_blocking(config: MailboxConfig) -> list[Inbound]:
    """Every unread mail in the inbox, oldest first. Fetching marks it read."""
    arrived: list[Inbound] = []
    client = _imap_connect(config)
    try:
        client.select("INBOX")
        status, found = client.search(None, "UNSEEN")
        if status != "OK":
            raise EmailError(f"IMAP search answered {status}")
        for number in (found[0] or b"").split():
            status, data = client.fetch(number, "(RFC822)")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            parsed = _parse(data[0][1])
            if parsed is not None:
                arrived.append(parsed)
    finally:
        # A dropped line during goodbye. The mail is already in hand, so this is
        # worth a debug line and nothing more.
        with contextlib.suppress(Exception):  # pragma: no cover
            client.logout()
    return arrived


def _send_blocking(
    config: MailboxConfig, *, to: str, subject: str, text: str, in_reply_to: str | None
) -> str:
    """One mail out through the channel's own SMTP. Returns its Message-ID.

    Raises on refusal rather than returning False: the callers store a line only
    after it went, and a boolean is how a refusal gets ignored by accident.
    """
    message = EmailMessage()
    message["From"] = config.from_address
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid()
    if in_reply_to:
        # What makes the answer land inside the customer's own thread, in their
        # client's idea of threading.
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(text)

    try:
        if config.smtp_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                config.smtp_host, config.smtp_port, timeout=15
            )
        else:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15)
            if config.smtp_tls:
                server.starttls()
        with server:
            server.login(config.username, config.password)
            server.send_message(message)
    except Exception as error:
        raise EmailError(str(error)) from error
    return str(message["Message-ID"])


def check_blocking(config: MailboxConfig) -> None:
    """Both halves answer, or this raises with which one did not.

    §A6.8's "Test connection", proving the link rather than claiming it — and both
    links, because a mailbox that receives but cannot send is an agent that reads
    customers' mail and never answers it.
    """
    try:
        client = _imap_connect(config)
        try:
            client.select("INBOX")
        finally:
            client.logout()
    except Exception as error:
        raise EmailError(f"IMAP: {error}") from error

    try:
        if config.smtp_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                config.smtp_host, config.smtp_port, timeout=10
            )
        else:
            server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=10)
            if config.smtp_tls:
                server.starttls()
        with server:
            server.login(config.username, config.password)
            server.noop()
    except Exception as error:
        raise EmailError(f"SMTP: {error}") from error


async def fetch_unseen(config: MailboxConfig) -> list[Inbound]:
    return await asyncio.to_thread(_fetch_unseen_blocking, config)


async def send_mail(
    config: MailboxConfig, *, to: str, subject: str, text: str, in_reply_to: str | None
) -> str:
    return await asyncio.to_thread(
        _send_blocking, config, to=to, subject=subject, text=text, in_reply_to=in_reply_to
    )


Fetch = Callable[[MailboxConfig], Awaitable[list[Inbound]]]
Send = Callable[..., Awaitable[str]]


# --- The conversation half -----------------------------------------------------


def reply_subject(subject: str) -> str:
    """`Re: <subject>`, once - a reply to a reply does not become `Re: Re:`."""
    return subject if subject.lower().startswith("re:") else f"Re: {subject}".strip()


async def _conversation_for(
    db: DbSession, channel: Channel, sender: str
) -> tuple[Conversation, bool]:
    row = await db.scalar(
        select(Conversation).where(
            Conversation.channel_id == channel.id,
            Conversation.external_id == sender,
            Conversation.status == "open",
        )
    )
    if row is not None:
        return row, False
    row = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=sender,
        handling="ai",
        status="open",
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row, True


async def _store_line(
    db: DbSession, conversation: Conversation, *, speaker: str, text: str
) -> Message:
    line = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        ts_ms=position_ms(conversation.started_at),
        speaker=speaker,
        text=text,
        language=None,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


async def _announce(
    db: DbSession, channel: Channel, conversation: Conversation, message: Message, started: bool
) -> None:
    try:
        if started:
            await webhooks.queue(
                db,
                workspace_id=channel.workspace_id,
                event="conversation.started",
                data={
                    "conversation": conversation.external_id,
                    "channel": "email",
                    "started_at": conversation.started_at.isoformat()
                    if conversation.started_at
                    else None,
                },
            )
        await webhooks.queue(
            db,
            workspace_id=channel.workspace_id,
            event="message.received",
            data={
                "conversation": conversation.external_id,
                "message_id": message.id,
                "speaker": "caller",
                "text": message.text,
                "ts_ms": message.ts_ms,
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "could not queue webhooks for a mail",
            extra={"conversation_id": conversation.id},
        )


def thread_state(conversation: Conversation) -> dict:
    return dict((conversation.state_json or {}).get("email") or {})


def remember_thread(conversation: Conversation, *, subject: str, message_id: str) -> None:
    """What a later reply needs to land in the customer's own thread."""
    conversation.state_json = {
        **(conversation.state_json or {}),
        "email": {"subject": subject, "last_message_id": message_id},
    }


PREVIEW_MAX = 80


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= PREVIEW_MAX else collapsed[: PREVIEW_MAX - 1] + "…"


async def _reply_and_send(
    db: DbSession,
    send: Send,
    config: MailboxConfig,
    channel: Channel,
    conversation: Conversation,
    incoming: Message,
) -> None:
    async def took(taken: TakenMessage) -> None:
        await raise_notification(
            db,
            workspace_id=channel.workspace_id,
            category="review",
            message_key="message_taken",
            params={"name": taken.name, "reason": _preview(taken.reason)},
            needs_decision=True,
            primary_action="open_conversation",
            action_payload={"conversation_id": conversation.id},
            conversation_id=conversation.id,
        )

    try:
        provider = await llm.resolve_provider(db)
    except ConfigurationError:
        logger.exception("email could not resolve a model", extra={"channel_id": channel.id})
        provider = None

    from api.routes.public_chat import _history

    history = await _history(db, conversation, incoming)

    pieces: list[str] = []
    async for chunk in generate_reply(
        incoming.text, provider=provider, history=history, on_message_taken=took
    ):
        pieces.append(chunk)
    whole = "".join(pieces)
    if not whole:
        return

    state = thread_state(conversation)
    # Sent before it is stored, the push-transport rule: a stored line that never
    # reached the customer would be a transcript lying about what the business said.
    await send(
        config,
        to=conversation.external_id or "",
        subject=reply_subject(str(state.get("subject") or "")),
        text=whole,
        in_reply_to=state.get("last_message_id") or None,
    )
    await _store_line(db, conversation, speaker="agent", text=whole)


async def _handle_inbound(
    db: DbSession,
    send: Send,
    config: MailboxConfig,
    channel: Channel,
    arrived: Inbound,
) -> None:
    if arrived.sender == config.from_address.strip().lower():
        # The channel's own address. Answering it is how a mailbox argues with
        # itself.
        return
    if not arrived.text:
        return

    conversation, started = await _conversation_for(db, channel, arrived.sender)
    remember_thread(conversation, subject=arrived.subject, message_id=arrived.message_id)
    line = await _store_line(db, conversation, speaker="caller", text=arrived.text)
    await _announce(db, channel, conversation, line, started)

    if arrived.auto_submitted:
        # An autoresponder's mail: part of the record, never answered. The loop this
        # prevents runs forever and costs a model call per lap.
        logger.info(
            "email reply withheld, the mail was auto-submitted",
            extra={"conversation_id": conversation.id},
        )
        return
    if conversation.handling == "human":
        # A colleague has the thread (§A6.7); the same silence as every channel.
        logger.info(
            "email reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return

    await _reply_and_send(db, send, config, channel, conversation, line)


async def _active_channels(db: DbSession) -> list[Channel]:
    return list(
        (
            await db.execute(
                select(Channel).where(Channel.kind == "email", Channel.status == "active")
            )
        )
        .scalars()
        .all()
    )


async def poll_once(
    db: DbSession, *, fetch: Fetch = fetch_unseen, send: Send = send_mail
) -> int:
    """One pass over every active email channel. Returns mails handled."""
    handled = 0
    for channel in await _active_channels(db):
        # Captured before anything can roll the session back: an expired ORM object
        # read for a log line raises from inside the except that wanted to log.
        channel_id = channel.id
        config = config_for(channel)
        if config is None:
            continue
        try:
            arrived = await fetch(config)
        except (EmailError, OSError) as error:
            logger.warning(
                "email poll failed",
                extra={"channel_id": channel_id, "error": str(error)[:200]},
            )
            continue
        for mail in arrived:
            try:
                await _handle_inbound(db, send, config, channel, mail)
                handled += 1
            except (EmailError, OSError):
                await db.rollback()
                logger.exception("mail handling failed", extra={"channel_id": channel_id})
                # The rollback expired every loaded object, and the next mail in this
                # batch still needs the channel's columns.
                await db.refresh(channel)
    return handled


async def loop(sessionmaker: async_sessionmaker) -> None:
    """The transport loop, started and cancelled by the app lifespan.

    Wrapped like the job loop's iterations: one bad pass must not end polling
    silently — a mailbox that stops being answered with no error anywhere is the
    failure §B8 exists to prevent.
    """
    while True:
        try:
            async with session_scope(sessionmaker) as db:
                await poll_once(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("email loop iteration failed")
        await asyncio.sleep(POLL_SECONDS)
