"""Sessions, stored — because a logout has to actually invalidate something.

A stateless token cannot be revoked. Signing out would only clear a cookie, and the
token would keep working for anyone who copied it. That matters more here than on a
hosted product: this is a box a family or a small office shares, and "sign out
everywhere" is a real request, not a checkbox.

**Only the hash of the token is stored.** The cookie holds the secret; the database
holds a fingerprint of it. A stolen database dump therefore contains no usable session,
which is the same reason `password_hash` exists rather than a password.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base
from api.models.common import utc_now_column
from api.models.identity import User


class Session(Base):
    """One signed-in browser."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # SHA-256 of the cookie value. Unique so a lookup is an index hit, and so two
    # sessions can never collide on one token.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[dt.datetime] = utc_now_column()
    last_seen_at: Mapped[dt.datetime] = utc_now_column()
    # Indexed: the cleanup path deletes by expiry, and without an index that becomes a
    # full scan of a table that only ever grows.
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Shown in a future "your sessions" screen so somebody can recognise which row is
    # the tablet in the back office. Truncated rather than rejected if absurdly long:
    # a header is attacker-controlled and must not be able to fail a sign-in.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    user: Mapped[User] = relationship()
