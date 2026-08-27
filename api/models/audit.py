"""Account events, recorded as they happen — because "did somebody get in" cannot be
answered retroactively.

One append-only table. Rows are written by `api/security/audit.py` and read by the
settings tab; nothing updates or deletes them from the application.

**No secret ever lands here.** Not a password, not a code, not a signature, not a
session token. The test suite greps the recorded rows for exactly that.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import utc_now_column

# The vocabulary, closed on purpose: a query over free-text event names degrades into
# guessing what past spellings were. Anything new is added here first.
EVENTS = (
    "login_succeeded",
    "login_failed",
    "login_locked",
    "logout",
    "logout_all",
    "password_changed",
    "password_reset",
    "second_factor_used",
    "key_sign_in_succeeded",
    "key_sign_in_failed",
    "recovery_code_requested",
)


class AuthEvent(Base):
    """One thing that happened to an account."""

    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Nullable: a failed sign-in against an unknown username has no user row to point
    # at, and that failure is precisely the kind worth recording.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The name as typed, kept even when `user_id` is set: after an account is deleted
    # the SET NULL above would otherwise erase who the row was about.
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
