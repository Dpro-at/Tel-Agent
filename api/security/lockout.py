"""Exponential backoff on the authentication routes.

**Backoff, not a permanent lock.** A lock that cannot expire by itself is a denial of
service anybody can trigger against the owner of the installation: five wrong guesses
against their username and the person who runs the business is locked out of their own
phone system with no way back in — on self-hosted software there is no support desk to
call. Doubling delays make guessing hopeless within a few attempts while the legitimate
owner is only ever inconvenienced for minutes.

The interface already expects this. The sign-in screen has a blocked state that shows
the time it unlocks at, and the reset screen says codes stop being sent for fifteen
minutes after five requests. The numbers here are those numbers.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models.attempt import AuthAttempt

logger = logging.getLogger("api.auth")

# Failures allowed before anything slows down. Four is enough for a person mistyping a
# password they know, and nowhere near enough to be useful to a guesser.
FREE_ATTEMPTS = 4

# The first lock, then doubling: 1, 2, 4, 8, 16, 32, 60, 60...
FIRST_LOCK = dt.timedelta(minutes=1)
MAX_LOCK = dt.timedelta(minutes=60)

# A counter that has seen nothing for this long starts again. Without it, four typos
# spread over a year eventually lock an account that was never under attack.
COUNTER_TTL = dt.timedelta(hours=12)


@dataclass(frozen=True)
class Lock:
    """An active lock, and when it lifts."""

    locked_until: dt.datetime

    @property
    def seconds_remaining(self) -> int:
        remaining = (self.locked_until - dt.datetime.now(dt.UTC)).total_seconds()
        return max(1, int(remaining))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes and PostgreSQL returns aware ones."""
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


# The number of doublings after which `FIRST_LOCK` already exceeds `MAX_LOCK`. Beyond
# this the answer is `MAX_LOCK` whatever the arithmetic says, so it is never computed:
# `2 ** 995` is a number Python is happy to build and `timedelta` cannot hold, and an
# attacker who keeps hammering would get a 500 instead of a lock.
_MAX_DOUBLINGS = 32


def _lock_for(failures: int) -> dt.timedelta | None:
    if failures <= FREE_ATTEMPTS:
        return None
    doublings = failures - FREE_ATTEMPTS - 1
    if doublings >= _MAX_DOUBLINGS:
        return MAX_LOCK
    return min(FIRST_LOCK * (2**doublings), MAX_LOCK)


async def _row(db: DbSession, scope: str, identifier: str, action: str) -> AuthAttempt | None:
    return await db.scalar(
        select(AuthAttempt).where(
            AuthAttempt.scope == scope,
            AuthAttempt.identifier == identifier,
            AuthAttempt.action == action,
        )
    )


async def check(
    db: DbSession, *, action: str, username: str | None, ip: str | None
) -> Lock | None:
    """Is either counter currently locked? Returns the one that lifts last."""
    locks: list[dt.datetime] = []

    for scope, identifier in (("account", username), ("ip", ip)):
        if not identifier:
            continue
        row = await _row(db, scope, identifier, action)
        if row is None or row.locked_until is None:
            continue
        until = _aware(row.locked_until)
        if until > _now():
            locks.append(until)

    return Lock(max(locks)) if locks else None


async def record_failure(
    db: DbSession, *, action: str, username: str | None, ip: str | None
) -> Lock | None:
    """Count a failure against both counters, and return the resulting lock if any."""
    now = _now()
    locks: list[dt.datetime] = []

    for scope, identifier in (("account", username), ("ip", ip)):
        if not identifier:
            continue

        row = await _row(db, scope, identifier, action)
        if row is None:
            row = AuthAttempt(
                scope=scope,
                identifier=identifier[:255],
                action=action,
                failures=0,
                first_failed_at=now,
                last_failed_at=now,
            )
            db.add(row)
        elif now - _aware(row.last_failed_at) > COUNTER_TTL:
            # Long quiet: this is not the same episode.
            row.failures = 0
            row.first_failed_at = now
            row.locked_until = None

        row.failures += 1
        row.last_failed_at = now

        window = _lock_for(row.failures)
        if window is not None:
            row.locked_until = now + window
            locks.append(row.locked_until)
            logger.info(
                "authentication locked",
                extra={
                    "scope": scope,
                    "action": action,
                    "failures": row.failures,
                    "seconds": int(window.total_seconds()),
                },
            )

    await db.commit()
    return Lock(max(locks)) if locks else None


async def clear(db: DbSession, *, action: str, username: str | None, ip: str | None) -> None:
    """Success wipes the counters.

    Both of them: a legitimate sign-in from an address proves that address is not
    mid-attack, and leaving its counter to expire on its own would punish a shared
    office router for one person's typos.
    """
    for scope, identifier in (("account", username), ("ip", ip)):
        if not identifier:
            continue
        await db.execute(
            delete(AuthAttempt).where(
                AuthAttempt.scope == scope,
                AuthAttempt.identifier == identifier,
                AuthAttempt.action == action,
            )
        )
    await db.commit()


async def delete_stale_counters(db: DbSession) -> int:
    """Cleanup path, alongside the expired-session one."""
    result = await db.execute(
        delete(AuthAttempt).where(AuthAttempt.last_failed_at <= _now() - COUNTER_TTL)
    )
    await db.commit()
    return result.rowcount or 0
