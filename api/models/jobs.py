"""Scheduled tasks and background jobs — the tables under P2.

Database-backed on purpose, not Redis-backed: this must work on every self-hosted
installation from the first boot, and the smallest installation is one process and one
SQLite file. Redis arrives later for live fan-out; nothing here will need rewriting
when it does.

Two tables because the two things recur differently. A **scheduled task** is a named
routine that runs forever on an interval — cleanup, health probes — and exists as
exactly one row updated in place. A **background job** is one unit of work that
happens once — send this email, deliver that webhook — enqueued, attempted with
backoff, and finished.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column

JOB_STATUSES = ("queued", "running", "done", "failed")


class ScheduledTask(Base):
    """One recurring routine. The row is the schedule, the registry holds the code.

    A row whose name has no registered handler is reported and skipped, never deleted:
    it usually means an extension that registered the task is disabled, and its
    schedule should survive until the extension returns.
    """

    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    interval_seconds: Mapped[int] = mapped_column(nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    # Indexed: the runner's whole question is "what is due", every tick.
    next_run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_run_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = utc_now_column()


class BackgroundJob(Base):
    """One unit of work, done off the request that asked for it."""

    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        enum_column(*JOB_STATUSES, name="job_status"), nullable=False, default="queued"
    )
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=5)
    # Indexed with status in mind: the claim query filters on both, every tick.
    next_attempt_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
