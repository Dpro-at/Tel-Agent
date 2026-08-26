"""Credentials that are not passwords: SSH keys, one-time codes, and password history.

Three tables, one theme — **nothing here stores a secret in a form that is usable if the
database leaks.** A public key is public by construction; a code and a password are
stored as hashes.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base
from api.models.common import enum_column, utc_now_column
from api.models.identity import User

# What a six-digit code authorises. The same screen serves both (D-030), and the two
# must not be interchangeable: a code issued to reset a password must never be accepted
# as a second factor, or the reset flow becomes a way around two-factor sign-in.
CODE_PURPOSES = ("reset", "second_factor")


class UserKey(Base):
    """An SSH public key registered against an account.

    Public by construction, so it is stored as-is. The private half never leaves the
    holder's machine: the server issues a challenge, they sign it locally with
    `ssh-keygen -Y sign`, and only the signature comes back.

    This is why the screen exists at all on self-hosted software — an administrator who
    never sets a password cannot have one guessed.
    """

    __tablename__ = "user_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The full authorized_keys line: `ssh-ed25519 AAAA... comment`.
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    # What the person calls it — "work laptop". Shown in settings so the right one can
    # be removed without decoding base64 to work out which is which.
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship()


class KeyChallenge(Base):
    """A challenge minted for one sign-in attempt.

    Stored rather than signed-and-stateless because it has to be **usable once**. A
    stateless challenge can be replayed by anyone who captured the signature; a row that
    is deleted on use cannot.
    """

    __tablename__ = "key_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The exact string the caller signs, shown on screen: `ta1-<uuid>`.
    challenge: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
    # Two minutes, as the screen says. Long enough to copy, sign and paste; short enough
    # that a challenge left on a screen overnight is worthless.
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class AuthCode(Base):
    """A six-digit code, stored as a hash.

    Six digits is a million possibilities, which is guessable in seconds if guessing is
    free. It is not free: three attempts per code, and the lockout counters in
    `api/security/lockout.py` throttle the rest.
    """

    __tablename__ = "auth_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(
        enum_column(*CODE_PURPOSES, name="code_purpose"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = utc_now_column()
    # Ten minutes, as the screen says.
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # The screen counts down from three: "That code does not match. 2 attempts left
    # before a new code is required."
    attempts_left: Mapped[int] = mapped_column(nullable=False, default=3)


class PasswordHistory(Base):
    """Fingerprints of recent passwords, so an old one cannot come back.

    The screen states this outright: "The server keeps the fingerprints of recent
    passwords so an old one cannot come back."

    A full Argon2 hash per entry, not a cheap digest: these are still password hashes,
    and a fast digest of a password is exactly what makes a leaked table worth cracking.
    """

    __tablename__ = "password_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.datetime] = utc_now_column()
