"""Failed authentication attempts, counted so they can be slowed down.

An unprotected login endpoint on a box reachable from a network is guessed at,
continuously, by software that never gets bored. This is the table that makes the
guessing expensive.

Counted per account *and* per address, because the two attacks are different. Guessing
one account's password is stopped by the account counter. Trying one common password
against every account — which the account counter never notices, since each account
sees a single failure — is stopped by the address counter.

Stored rather than held in memory: a restart must not hand an attacker a fresh budget,
and `--reload` in development restarts constantly.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column

SCOPES = ("account", "ip")


class AuthAttempt(Base):
    """One counter, for one account or one address, on one kind of action."""

    __tablename__ = "auth_attempts"
    __table_args__ = (
        UniqueConstraint("scope", "identifier", "action", name="scope_identifier_action"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(
        enum_column(*SCOPES, name="attempt_scope"), nullable=False
    )
    # A username or an IP address. Never a password, and never anything derived from one.
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    # `login`, `code`, `forgot`, `password` — each throttled separately, so exhausting
    # one does not lock a person out of the route that would let them recover.
    action: Mapped[str] = mapped_column(String(32), nullable=False)

    failures: Mapped[int] = mapped_column(nullable=False, default=0)
    first_failed_at: Mapped[dt.datetime] = utc_now_column()
    last_failed_at: Mapped[dt.datetime] = utc_now_column()
    # Null until the threshold is crossed. Indexed so the cleanup path can find rows
    # whose lock has long expired without scanning the table.
    locked_until: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
