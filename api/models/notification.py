"""Notifications — P4.

The screen already says what this table is for, and it makes an unusual distinction
worth honouring exactly: *"Two kinds of thing live here. The ones at the top are
waiting on a decision."* Everything below is a log of what already happened.

That is not a read/unread flag. A webhook that recovered by itself is **information**
and needs no reply; an SMS the agent promised and never sent is a **decision** somebody
has to make. Marking the second one "read" would file it away without the decision ever
being taken, which is precisely the failure the home screen's "3 things are waiting on a
decision" exists to prevent.

So a notification carries two independent facts:

* `needs_decision` — does this require a human to act? Set at creation, never toggled.
* `resolved_at` — has the action been taken, or the information acknowledged?

An item waiting on a decision leaves the top of the screen only when it is genuinely
resolved: the SMS was resent, the number was allowed, the fallback was set. "Mark all as
read" therefore resolves the log and leaves the decisions alone — which is what the
screen's two headings promise, and what a single `is_read` column could not express.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk

# The filter chips on the screen, in its own words: Failures, Review, Missed, System.
CATEGORIES = ("failure", "review", "missed", "system")

# What the primary button does. Stored rather than derived from the category, because
# two failures can want different repairs - a failed SMS is resent, a failed webhook is
# retried - and a screen that guesses from the category guesses wrong eventually.
ACTIONS = (
    "resend_notification",
    "retry_webhook",
    "allow_number",
    "edit_rule",
    "set_fallback",
    "open_conversation",
    "none",
)


class Notification(Base):
    """One thing the operator should know about, or decide."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()

    category: Mapped[str] = mapped_column(
        enum_column(*CATEGORIES, name="notification_category"), nullable=False, index=True
    )
    # Whether a human has to do something. Fixed at creation: an item does not become
    # informational because nobody got round to it.
    needs_decision: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)

    # **A key, not a sentence.** The screen is translated into three languages and a
    # notification is read on it; prose written by whatever raised the notification
    # would arrive in the server's language and stay there, so a German receptionist
    # would read English on an otherwise German screen.
    #
    # The key names the situation - `backup_failed` - and the parameters carry the
    # parts that vary. The catalogue in `api/notifications.py` declares which
    # parameters each key requires, so a caller that forgets one is a failure at the
    # call site rather than a `{reason}` printed literally on somebody's screen.
    message_key: Mapped[str] = mapped_column(String(64), nullable=False)
    # Values interpolated into the translated string. JSON because each message wants
    # different ones, and a column per parameter would be a column that is null for
    # every other message.
    #
    # **Never a secret and never personal data beyond what the message needs.** These
    # are stored for thirty days and read by anybody with `viewer`.
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    primary_action: Mapped[str] = mapped_column(
        enum_column(*ACTIONS, name="notification_action"), nullable=False, default="none"
    )
    # What the action needs to run: a number to allow, a rule to open, a message to
    # resend. JSON because each action wants a different shape, and a column per action
    # would be a column that is null for every other kind.
    action_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    # What this is about, when it is about something. Nullable and without a foreign
    # key on purpose: a notification must outlive the conversation it refers to, or
    # deleting a transcript would erase the record that something went wrong with it.
    conversation_id: Mapped[int | None] = mapped_column(nullable=True, index=True)

    created_at: Mapped[dt.datetime] = utc_now_column()
    # Null while open. Indexed together with the workspace because "what is open here"
    # is the query the screen and the home badge both run on every load.
    resolved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
