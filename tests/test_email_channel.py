"""The email channel — §B13's third no-platform transport, and its settings card.

The mailbox is a stand-in throughout: `poll_once` takes its fetch and send halves as
parameters, so the tests hand it functions instead of an IMAP server — and exercise
this product's half of the conversation, which is where every rule lives. The wire
half (`imaplib`/`smtplib`) is deliberately thin and mirrors `api/mail.py`, which the
mail-test button already proves against a real server.

The rules under test are the channel contract, on its third transport: one open
conversation per correspondent, the takeover silence, delivery before storage — and
the one rule email adds: **an auto-submitted mail is stored and never answered**,
because an agent that answers an out-of-office answers it forever.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.reply import GREETING
from api.channels import email as email_transport
from api.channels.email import Inbound, MailboxConfig
from api.config import Settings
from api.main import create_app
from api.models import (
    BackgroundJob,
    Channel,
    Conversation,
    Membership,
    Message,
    User,
    Webhook,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32

MAILBOX = {
    "imap_host": "imap.wagner-partner.test",
    "imap_port": 993,
    "smtp_host": "smtp.wagner-partner.test",
    "smtp_port": 587,
    "username": "reception@wagner-partner.test",
    "from_address": "reception@wagner-partner.test",
}


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    """The mailbox password lives in an encrypted column, so a key is not optional."""
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


class FakeMailbox:
    """The two halves `poll_once` takes, plus a switch that refuses everything."""

    def __init__(self) -> None:
        self.inbox: list[Inbound] = []
        self.sent: list[dict] = []
        self.refuse_send = False

    async def fetch(self, config: MailboxConfig) -> list[Inbound]:
        arrived, self.inbox = self.inbox, []
        return arrived

    async def send(
        self,
        config: MailboxConfig,
        *,
        to: str,
        subject: str,
        text: str,
        in_reply_to: str | None,
    ) -> str:
        if self.refuse_send:
            raise email_transport.EmailError("SMTP: refused")
        self.sent.append(
            {"to": to, "subject": subject, "text": text, "in_reply_to": in_reply_to}
        )
        return f"<sent-{len(self.sent)}@wagner-partner.test>"


def _mail(
    sender: str = "anna@example.test",
    *,
    subject: str = "Saturday",
    text: str = "Do you open on Saturday?",
    message_id: str = "<m1@example.test>",
    auto: bool = False,
) -> Inbound:
    return Inbound(
        sender=sender,
        subject=subject,
        text=text,
        message_id=message_id,
        auto_submitted=auto,
    )


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str, monkeypatch):
    """One workspace with an active email channel, and the fake mailbox."""
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    channel = Channel(
        workspace_id=mine.id,
        kind="email",
        name="Email",
        credentials_encrypted="mailbox-password",
        settings_json=dict(MAILBOX),
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
    fake = FakeMailbox()
    # What the takeover reply route reaches for; `poll_once` gets it as a parameter.
    monkeypatch.setattr(email_transport, "send_mail", fake.send)

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "sabine", "lukas"):
            http = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients, ids, fake, migrated
        finally:
            for http in clients.values():
                await http.aclose()


async def _poll(db: AsyncSession, fake: FakeMailbox) -> int:
    return await email_transport.poll_once(db, fetch=fake.fetch, send=fake.send)


# --- The transport ------------------------------------------------------------


async def test_a_mail_becomes_a_conversation_and_the_agent_answers_in_thread(stage) -> None:
    _, _, fake, db = stage
    fake.inbox = [_mail()]

    handled = await _poll(db, fake)
    assert handled == 1

    db.expire_all()
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == "anna@example.test")
    )
    assert thread is not None
    rows = (
        (
            await db.execute(
                select(Message).where(Message.conversation_id == thread.id).order_by(Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(r.speaker, r.text) for r in rows] == [
        ("caller", "Do you open on Saturday?"),
        ("agent", GREETING),
    ]
    # The answer left as a proper reply: same thread in the customer's own client.
    assert fake.sent == [
        {
            "to": "anna@example.test",
            "subject": "Re: Saturday",
            "text": GREETING,
            "in_reply_to": "<m1@example.test>",
        }
    ]


async def test_a_second_mail_continues_the_same_conversation(stage) -> None:
    _, _, fake, db = stage
    fake.inbox = [_mail()]
    await _poll(db, fake)
    fake.inbox = [_mail(subject="Re: Saturday", text="And Sunday?", message_id="<m2@e.test>")]
    await _poll(db, fake)

    db.expire_all()
    threads = (
        (
            await db.execute(
                select(Conversation).where(Conversation.external_id == "anna@example.test")
            )
        )
        .scalars()
        .all()
    )
    assert len(threads) == 1
    # A reply to a reply does not become `Re: Re:`.
    assert fake.sent[-1]["subject"] == "Re: Saturday"
    assert fake.sent[-1]["in_reply_to"] == "<m2@e.test>"


async def test_an_auto_submitted_mail_is_stored_and_never_answered(stage) -> None:
    """The loop that never ends: an agent answering an out-of-office."""
    _, _, fake, db = stage
    fake.inbox = [_mail(text="I am out of office.", auto=True)]
    await _poll(db, fake)

    assert fake.sent == []
    db.expire_all()
    rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    assert [r.speaker for r in rows] == ["caller"]


async def test_mail_from_the_channels_own_address_is_skipped_whole(stage) -> None:
    _, _, fake, db = stage
    fake.inbox = [_mail(sender="reception@wagner-partner.test")]
    await _poll(db, fake)

    assert fake.sent == []
    db.expire_all()
    assert (await db.execute(select(Conversation))).scalars().all() == []


async def test_a_taken_over_thread_gets_no_generated_reply(stage) -> None:
    """§A6.7's silence rule, holding on the third transport."""
    _, _, fake, db = stage
    fake.inbox = [_mail(text="I would rather talk to a person.")]
    await _poll(db, fake)

    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == "anna@example.test")
    )
    thread.handling = "human"
    await db.commit()
    fake.sent.clear()

    fake.inbox = [_mail(text="So is anyone there?", message_id="<m2@e.test>")]
    await _poll(db, fake)
    assert fake.sent == []


async def test_a_refused_send_leaves_no_agent_line_in_the_record(stage) -> None:
    """Delivery before storage — a transcript must not say what nobody received."""
    _, _, fake, db = stage
    fake.refuse_send = True
    fake.inbox = [_mail()]
    await _poll(db, fake)

    db.expire_all()
    rows = (await db.execute(select(Message).order_by(Message.id))).scalars().all()
    assert [r.speaker for r in rows] == ["caller"]


async def test_the_registered_hooks_are_told_like_every_channel(stage) -> None:
    _, ids, fake, db = stage
    db.add(
        Webhook(
            workspace_id=ids["workspace"],
            url="https://wagner-partner.test/hooks",
            events=["conversation.started", "message.received"],
            secret="a-shared-secret",  # noqa: S106
            enabled=True,
        )
    )
    await db.commit()

    fake.inbox = [_mail()]
    await _poll(db, fake)

    queued = (
        await db.scalars(select(BackgroundJob).where(BackgroundJob.kind == "webhook"))
    ).all()
    started = next(j for j in queued if j.payload["event"] == "conversation.started")
    assert started.payload["data"]["channel"] == "email"


async def test_quoted_reply_history_is_stripped_before_the_record(stage) -> None:
    text = (
        "Sunday would work.\n\n"
        "On Mon, 31 Aug 2026, Reception wrote:\n"
        "> We open Saturday until noon.\n"
        "> Anything else?\n"
    )
    assert email_transport.strip_quotes(text) == "Sunday would work."


# --- The settings card --------------------------------------------------------


async def test_the_password_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/email", json={"password": "fresh-secret"}
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert "fresh-secret" not in json.dumps(body)
    assert body["password_preview"].endswith("cret")


async def test_sending_the_mask_back_does_not_overwrite_the_password(stage) -> None:
    clients, ids, _, db = stage
    preview = (await clients["mohamed"].get("/api/channels/email")).json()["password_preview"]
    assert preview is not None
    assert (
        await clients["mohamed"].put("/api/channels/email", json={"password": preview})
    ).status_code == 200

    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.id == ids["channel"]))
    assert row.credentials_encrypted == "mailbox-password"


async def test_removing_the_password_switches_the_channel_off_with_it(stage) -> None:
    clients, _, _, _ = stage
    saved = await clients["mohamed"].put("/api/channels/email", json={"password": ""})
    assert saved.status_code == 200
    assert saved.json()["enabled"] is False
    assert saved.json()["password_preview"] is None

    refused = await clients["mohamed"].put("/api/channels/email", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "mailbox_incomplete"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/email")).status_code == 200
    refused = await clients["lukas"].put("/api/channels/email", json={"enabled": False})
    assert refused.status_code == 403


async def test_the_connection_test_refuses_an_incomplete_mailbox(stage) -> None:
    clients, _, _, _ = stage
    await clients["mohamed"].put("/api/channels/email", json={"password": ""})
    answer = await clients["mohamed"].post("/api/channels/email/test")
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "mailbox_incomplete"


# --- The takeover reply, delivered --------------------------------------------


async def _taken_thread(fake: FakeMailbox, db: AsyncSession) -> Conversation:
    fake.inbox = [_mail(text="I would rather talk to a person.")]
    await email_transport.poll_once(db, fetch=fake.fetch, send=fake.send)
    thread = await db.scalar(
        select(Conversation).where(Conversation.external_id == "anna@example.test")
    )
    thread.handling = "human"
    await db.commit()
    fake.sent.clear()
    return thread


async def test_a_human_reply_reaches_the_mailbox_in_thread(stage) -> None:
    clients, _, fake, db = stage
    thread = await _taken_thread(fake, db)

    sent = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply",
        json={"text": "Yes — this is Sabine. We open Saturday until noon."},
    )
    assert sent.status_code == 201, sent.text
    assert fake.sent == [
        {
            "to": "anna@example.test",
            "subject": "Re: Saturday",
            "text": "Yes — this is Sabine. We open Saturday until noon.",
            "in_reply_to": "<m1@example.test>",
        }
    ]


async def test_an_undelivered_reply_is_not_written_into_the_record(stage) -> None:
    clients, _, fake, db = stage
    thread = await _taken_thread(fake, db)
    before = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))

    fake.refuse_send = True
    refused = await clients["sabine"].post(
        f"/api/conversations/{thread.id}/reply", json={"text": "Hello?"}
    )
    assert refused.status_code == 502
    assert refused.json()["error"]["code"] == "not_delivered"

    db.expire_all()
    last = await db.scalar(select(Message).order_by(Message.id.desc()).limit(1))
    assert last.id == before.id
