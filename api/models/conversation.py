"""The core of the product: conversations, and the lines inside them.

**`conversations` is the master table, not `calls`** (§B5 decision 6). A phone call is a
conversation whose channel is of kind `phone`, plus a `calls` row carrying the four
things only a phone call has. A web chat is the same conversation row with a different
channel and no `calls` row.

That split is what makes full-text search, the archive, routing rules, `take_message`,
tool invocations and the live view work on every channel without a branch — and it is
why the schema is written this way now, when there is no stored row, rather than
migrated into this shape after Milestone 2.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk
from api.models.encrypted import EncryptedStr

# The ten channels Tel-Agent commits to. The list is no longer closed (D-032) — a
# channel is an extension — but these are the kinds the core ships support for, and
# `channels.app_id` is what points at the extension that actually implements one.
CHANNEL_KINDS = (
    "web",
    "phone",
    "sms",
    "email",
    "whatsapp",
    "telegram",
    "messenger",
    "instagram",
    "discord",
    "slack",
)


class Channel(Base):
    """A route a customer uses to reach a business.

    Not a system the business itself runs on — that is an integration, reached through
    the HTTP tool. The line matters enough that Rule 5 exists to hold it.
    """

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    kind: Mapped[str] = mapped_column(
        enum_column(*CHANNEL_KINDS, name="channel_kind"), nullable=False
    )
    # The extension implementing this channel (D-031/D-032). Nullable while the core
    # still serves `web` directly; it is filled in as each channel becomes an app.
    app_id: Mapped[int | None] = mapped_column(
        ForeignKey("apps.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # §B9.2: user-entered credentials live in the database, encrypted, never in `.env`.
    # `EncryptedStr` encrypts on write and decrypts on read, so no call site can
    # forget - the model attribute holds the plaintext, the stored column never does.
    credentials_encrypted: Mapped[str | None] = mapped_column(EncryptedStr, nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    webhook_path: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    default_language: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # FK arrives with the `agents` table; the column is here because §B5 puts it here.
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        enum_column("active", "disabled", "error", name="channel_status"),
        nullable=False,
        default="active",
    )
    created_at: Mapped[dt.datetime] = utc_now_column()

    conversations: Mapped[list[Conversation]] = relationship(back_populates="channel")


class Number(Base):
    """A phone number, and who holds it.

    `owner` is §B5 decision 3, and it is here in the first migration for one reason:
    once both kinds of number exist, backfilling it means guessing. It separates a
    self-hoster's own number from one resold by Tel-Agent Cloud, and it governs who may
    release or port it.
    """

    __tablename__ = "numbers"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_account_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner: Mapped[str] = mapped_column(
        enum_column("customer", "platform", name="number_owner"), nullable=False
    )
    e164: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    sip_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        enum_column("active", "disabled", name="number_status"),
        nullable=False,
        default="active",
    )
    created_at: Mapped[dt.datetime] = utc_now_column()


class Conversation(Base):
    """One exchange with one customer, on any channel."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # FK arrives with the `contacts` table.
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The identifier the channel itself uses — a Telegram chat id, an email thread id.
    # Indexed because every inbound message arrives with one and has to find its thread.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(
        enum_column("inbound", "outbound", name="direction"), nullable=False
    )
    started_at: Mapped[dt.datetime] = utc_now_column()
    ended_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    handling: Mapped[str | None] = mapped_column(
        enum_column("ai", "human", "blocked", name="handling"), nullable=True
    )
    intent: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        enum_column("open", "closed", name="conversation_status"),
        nullable=False,
        default="open",
    )

    channel: Mapped[Channel] = relationship(back_populates="conversations")
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    call: Mapped[Call | None] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )


class Call(Base):
    """What only a phone call has.

    Four columns, and two of them are §B5 decision 4: usage metering from the first
    stored call. `provider_cost_micros` is an integer of millionths — never a float.
    Money in binary floating point does not add up, and a billing total that is wrong by
    a rounding error is a billing total nobody trusts again.
    """

    __tablename__ = "calls"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    workspace_id: Mapped[int] = workspace_fk()
    number_id: Mapped[int | None] = mapped_column(
        ForeignKey("numbers.id", ondelete="SET NULL"), nullable=True
    )
    from_e164: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # Personal data under GDPR. The file stays on the machine that produced it; this is
    # only the path to it.
    recording_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    billable_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="call")


class Message(Base):
    """One line in a conversation, typed or spoken."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Denormalised from the parent conversation, deliberately.
    #
    # D-028 says every data table carries the tenant key, and `messages` is the table
    # where forgetting it costs the most: it holds the transcripts, and it is what
    # full-text search runs over. Reaching the workspace through a join would make the
    # single hottest query in the product the one most likely to be written without a
    # scope. The invariant - a message's workspace always matches its conversation's -
    # is held by writing messages only through their conversation.
    workspace_id: Mapped[int] = workspace_fk()
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Milliseconds since the conversation started, not a wall clock. On a call the
    # position within the recording is what a transcript is read against, and a clock
    # adjustment mid-call must not be able to reorder the lines.
    ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    speaker: Mapped[str] = mapped_column(
        enum_column("caller", "agent", "human", name="speaker"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # An operator's instruction to the agent, mid-conversation. Stored in the transcript
    # because it is part of what happened, flagged because the customer never saw it.
    is_whisper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # §B5 decision 5, per line rather than per conversation.
    #
    # Both are null on text channels, and that null is itself the signal that the line
    # was typed rather than spoken. Stored per line, confidence turns "German accuracy"
    # from an impression into a query — *show every line under 0.7* — and the failure
    # pattern surfaces on its own instead of being found by replaying recordings.
    stt_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    language: Mapped[str | None] = mapped_column(String(12), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
