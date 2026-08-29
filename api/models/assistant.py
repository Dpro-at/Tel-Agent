"""Assistants — who answers, and in what words.

§A6.6 draws one editor with ten panels: persona, instructions, knowledge, contacts,
booking, apps, webhooks, email, sms and forward. Nine of them are windows onto other
subsystems, and each arrives with the subsystem it reads. What is left, and what this
table is, is the part an assistant owns outright: its name, the model behind it, and
the two blocks of text a customer writes to shape how it answers.

**No voice, no speed, no opening lines, no barge-in.** Those describe a call, and
calls are the last thing built rather than the first - so a column for any of them
would be a promise this table cannot keep. They arrive with the channel that needs
them, which is also when their defaults stop being guesses.

**No `is_default` either.** Which assistant answers a given contact is a routing
question, and routing already has a table. Two places deciding the same thing is how
they come to disagree.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk

# What the customer started from. Kept because "which template was this" survives
# every later edit and is the only way the screen can offer to reset one.
ASSISTANT_TEMPLATES = ("reception", "ooh", "overflow", "blank")

ASSISTANT_STATUSES = ("active", "paused")

# §B7's list, verbatim and short on purpose - "five precise tools beat twenty that
# confuse the model". Which of them an assistant may reach is per assistant, because
# an out-of-hours assistant that only takes messages is a different thing from a
# reception assistant that answers questions.
#
# The list is the specification's, not "the ones that work today". A tool whose
# subsystem is unbuilt is reported as unavailable rather than hidden: a person
# choosing what their assistant can do should see the whole shape of the answer.
ASSISTANT_TOOLS = (
    "take_message",
    "search_knowledge",
    "http_request",
    "send_notification",
    "check_calendar",
    "transfer_call",
    "end_call",
)


class Assistant(Base):
    """One assistant: a name, a model, and the words that shape its answers."""

    __tablename__ = "assistants"
    # Two assistants called "Lena" in one workspace is a support call waiting to
    # happen - every screen that refers to one refers to it by name.
    __table_args__ = (UniqueConstraint("workspace_id", "name", name="assistant_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # What it is for, in the customer's words - "Reception, weekdays". The design
    # showed this as interface copy; it is data, because only the customer knows
    # which of their assistants does what.
    role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    template: Mapped[str] = mapped_column(
        enum_column(*ASSISTANT_TEMPLATES, name="assistant_template"),
        nullable=False,
        default="blank",
    )
    status: Mapped[str] = mapped_column(
        enum_column(*ASSISTANT_STATUSES, name="assistant_status"),
        nullable=False,
        default="active",
    )
    # The two texts §A6.6 separates. `persona` is who it is; `instructions` is what
    # it may and may not do. They are one prompt to the model and two boxes to the
    # person writing them, and keeping them apart is what makes the second editable
    # without rewriting the first.
    persona: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # A BCP-47 tag, or NULL for "answer in whatever language the customer wrote in",
    # which is the useful default and the one the agent already does.
    language: Mapped[str | None] = mapped_column(String(12), nullable=True)
    # Free text on purpose. Model names are a moving list owned by three vendors, and
    # a CHECK constraint here would need a migration every time one ships a model.
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # A JSON list rather than a join table, for the reason the webhook events are one:
    # a vocabulary this codebase pins, read only alongside its own row, never queried
    # across rows. Empty is a real answer - an assistant that only talks.
    tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = utc_now_column()
    updated_at: Mapped[dt.datetime] = utc_now_column()
