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
    # The workspaces epic. Written against the *affected* account, not the acting one:
    # "your role here changed" and "you were removed" are facts about the person they
    # happened to, and the settings tab shows each person their own trail. Who did it
    # goes in `details`.
    "role_changed",
    "member_removed",
    "workspace_created",
    "invite_created",
    "invite_accepted",
    # P7. Not account events in the narrow sense, and recorded here anyway: one backup
    # archive is every transcript on the installation, so downloading one is a data
    # export, and staging a restore is the only action in the product that deletes
    # everything since a date. Both need a name attached to them afterwards.
    "backup_downloaded",
    "backup_deleted",
    "restore_staged",
    # The numbers registry. A number is how customers reach the business, so adding
    # one, disabling one, and above all releasing one need a name attached to them
    # afterwards. Recorded against the acting account.
    "number_added",
    "number_status_changed",
    "number_released",
    # Routing rules. Blocking a caller, or unblocking one, changes who can reach the
    # business - a fact worth a name afterwards. Recorded against the acting account.
    "rule_added",
    "rule_changed",
    "rule_removed",
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
