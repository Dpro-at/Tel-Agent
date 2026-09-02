"""Routing rules — what happens when a known number calls.

§B5's own row: `e164_or_pattern, action(pass|block|ai), note`. Three outcomes and
only three (§A6.5): straight through to a person, refused, or answered by the
agent. The screen draws them as three columns, and the columns are the vocabulary.

**The table is the record; the agent is the judge.** Nothing here executes a rule
— that happens in `agent/` at Milestone 11, where the matching order (exact number
before prefix) is the agent's to enforce. What the dashboard owns is which rules
exist, which is why the model carries no `hits` counter and no priority column:
neither is a fact until something matches calls against it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk

RULE_ACTIONS = ("pass", "block", "ai")


class Rule(Base):
    """One caller pattern, and what a call from it gets."""

    __tablename__ = "rules"
    # One rule per pattern per workspace: two rows for the same number with two
    # actions would leave the agent to pick one silently.
    __table_args__ = (UniqueConstraint("workspace_id", "pattern", name="rule_pattern"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    # A channel identity, exact or as a prefix ending in `*`: an E.164, an email
    # address, a Telegram chat id or @username, a Slack or Discord user id — Milestone
    # 4 renamed this from `e164_or_pattern` because a matcher built phone-only would
    # have been written twice (the roadmap's own words). 320 fits the longest of
    # them, an email address. Matching is `api/routing.py`'s, case-insensitive.
    pattern: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(
        enum_column(*RULE_ACTIONS, name="rule_action"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
