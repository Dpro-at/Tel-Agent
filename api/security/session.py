"""Creating, resolving and revoking sessions — and the current-user dependency.

The cookie holds a random token. The database holds its SHA-256. Nothing anywhere
stores the token itself, so a database dump yields no usable session.

SHA-256 rather than Argon2 here, deliberately. A password is low-entropy and chosen by
a person, so it needs a slow hash to survive being guessed. A session token is 256 bits
from the system's random source: there is nothing to guess, and the lookup happens on
every single request — a deliberately slow hash would put Argon2's cost on the hot path
for no security gained.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Session, User

logger = logging.getLogger("api.auth")

COOKIE_NAME = "telagent_session"

# Fourteen days. Long enough that a reception desk is not signing in every morning,
# short enough that a forgotten browser stops being a way in within a fortnight.
SESSION_LIFETIME = dt.timedelta(days=14)

# `last_seen_at` is written at most this often. Every request would mean a write on
# every request, including the ones that only read.
LAST_SEEN_RESOLUTION = dt.timedelta(minutes=5)

# 32 bytes from the system CSPRNG, URL-safe. Guessing is not a threat model at this
# size; the threats are theft and failure to revoke, which is why sessions are rows.
_TOKEN_BYTES = 32


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; PostgreSQL hands back aware ones.

    Comparing the two raises, so everything read from the database is normalised to UTC
    here. Without this the expiry check works on one dialect and crashes on the other.
    """
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


async def create_session(
    db: DbSession,
    user: User,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> str:
    """Start a session and return the token to put in the cookie."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    now = _now()
    db.add(
        Session(
            token_hash=hash_token(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_LIFETIME,
            # A header is attacker-controlled: truncated, never allowed to fail a
            # sign-in by being too long for the column.
            user_agent=(user_agent or "")[:255] or None,
            ip=ip,
        )
    )
    await db.commit()
    return token


async def resolve_session(db: DbSession, token: str | None) -> Session | None:
    """Find the live session for this token, or None.

    An expired row is deleted rather than merely ignored. Leaving it means the table
    only ever grows, and a row that can never authorise anything is a row worth being
    rid of at the moment it is noticed.
    """
    if not token:
        return None

    session = await db.scalar(select(Session).where(Session.token_hash == hash_token(token)))
    if session is None:
        return None

    now = _now()
    if _as_aware(session.expires_at) <= now:
        await db.delete(session)
        await db.commit()
        return None

    if now - _as_aware(session.last_seen_at) > LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
        await db.commit()

    return session


async def revoke_session(db: DbSession, token: str) -> None:
    """End this one session. Clearing the cookie alone would leave the row valid."""
    await db.execute(delete(Session).where(Session.token_hash == hash_token(token)))
    await db.commit()


async def revoke_all_other_sessions(db: DbSession, user_id: int, keep_token: str) -> int:
    """Sign out everywhere else, and return how many were ended.

    Keeping the current one is what makes this usable: an owner who suspects a
    forgotten tablet in the back office should not have to sign in again themselves.
    """
    result = await db.execute(
        delete(Session).where(
            Session.user_id == user_id,
            Session.token_hash != hash_token(keep_token),
        )
    )
    await db.commit()
    return result.rowcount or 0


async def delete_expired_sessions(db: DbSession) -> int:
    """Cleanup path. Deletes by `expires_at`, which is indexed for exactly this."""
    result = await db.execute(delete(Session).where(Session.expires_at <= _now()))
    await db.commit()
    return result.rowcount or 0


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    """Attach the cookie.

    - `HttpOnly`: script cannot read it, so an XSS bug cannot exfiltrate the session.
    - `SameSite=Lax`: it is not sent on cross-site POSTs, which removes the simplest
      form of CSRF. D7 adds the rest; `Lax` alone is not a complete defence.
    - `Secure` in production only. Setting it in development would break `http://
      localhost`, and a developer who cannot sign in disables the flag everywhere.
    """
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=secure)


def token_from_request(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)
