"""Repeated failures are throttled, and the backoff is provable — D8.

The acceptance condition is the last clause: a lock that grows is only useful if it can
be shown to grow, and a lock that never lifts is a denial of service anybody can point
at the owner of the installation.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import AuthAttempt
from api.security import lockout
from api.security.session import COOKIE_NAME
from api.setup import create_first_administrator

USERNAME = "wagner"
PASSWORD = "a sentence i can actually remember"  # noqa: S105
WRONG = "definitely not the password"


@pytest.fixture
async def signed_up(migrated: AsyncSession, settings: Settings, database_url: str):
    await create_first_administrator(
        migrated, username=USERNAME, password=PASSWORD, workspace_name="Wagner & Partner"
    )
    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client


async def _attempt(client: AsyncClient, password: str):
    return await client.post(
        "/api/auth/login", json={"username": USERNAME, "password": password}
    )


# --- The backoff itself ------------------------------------------------------


def test_the_first_failures_are_free() -> None:
    """A person mistyping a password they know must not be punished for it."""
    for failures in range(1, lockout.FREE_ATTEMPTS + 1):
        assert lockout._lock_for(failures) is None


def test_the_lock_doubles() -> None:
    """1, 2, 4, 8, 16, 32 minutes — guessing becomes hopeless within a few attempts."""
    windows = [lockout._lock_for(lockout.FREE_ATTEMPTS + n) for n in range(1, 7)]

    assert windows == [
        dt.timedelta(minutes=1),
        dt.timedelta(minutes=2),
        dt.timedelta(minutes=4),
        dt.timedelta(minutes=8),
        dt.timedelta(minutes=16),
        dt.timedelta(minutes=32),
    ]


def test_the_lock_is_capped_and_never_permanent() -> None:
    """The important half of D8.

    A lock that cannot expire is a denial of service anyone can trigger against the
    owner — and on self-hosted software there is no support desk to call.
    """
    for failures in (20, 50, 1000):
        window = lockout._lock_for(failures)
        assert window is not None
        assert window <= lockout.MAX_LOCK
        assert window.total_seconds() > 0


# --- Through the endpoint ----------------------------------------------------


async def test_repeated_failures_are_throttled(signed_up: AsyncClient) -> None:
    for _ in range(lockout.FREE_ATTEMPTS):
        assert (await _attempt(signed_up, WRONG)).status_code == 401

    refused = await _attempt(signed_up, WRONG)

    assert refused.status_code == 429
    error = refused.json()["error"]
    assert error["code"] == "rate_limited"
    # The screen shows the time it unlocks at, so the endpoint has to say when.
    assert error["details"][0]["seconds_remaining"] > 0
    assert error["details"][0]["locked_until"]


async def test_the_right_password_is_refused_while_locked(signed_up: AsyncClient) -> None:
    """Otherwise the lock is only an inconvenience to somebody who is already guessing."""
    for _ in range(lockout.FREE_ATTEMPTS + 1):
        await _attempt(signed_up, WRONG)

    response = await _attempt(signed_up, PASSWORD)

    assert response.status_code == 429
    assert COOKIE_NAME not in response.cookies


async def test_the_lock_lifts_on_its_own(
    signed_up: AsyncClient, migrated: AsyncSession
) -> None:
    """Time passing is enough. Nobody has to intervene, because nobody could."""
    for _ in range(lockout.FREE_ATTEMPTS + 1):
        await _attempt(signed_up, WRONG)
    assert (await _attempt(signed_up, PASSWORD)).status_code == 429

    # Wind the clock forward rather than waiting a minute for it.
    for row in (await migrated.execute(select(AuthAttempt))).scalars():
        row.locked_until = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    await migrated.commit()

    response = await _attempt(signed_up, PASSWORD)

    assert response.status_code == 200
    assert response.cookies[COOKIE_NAME]


async def test_a_successful_sign_in_clears_the_counters(
    signed_up: AsyncClient, migrated: AsyncSession
) -> None:
    """Three typos then the right password leaves nothing behind."""
    for _ in range(3):
        await _attempt(signed_up, WRONG)
    assert (await migrated.execute(select(AuthAttempt))).scalars().all()

    assert (await _attempt(signed_up, PASSWORD)).status_code == 200

    migrated.expire_all()
    assert (await migrated.execute(select(AuthAttempt))).scalars().all() == []


async def test_an_unknown_username_is_counted_too(
    signed_up: AsyncClient, migrated: AsyncSession
) -> None:
    """Counting only real accounts would make an unknown username cheap to test.

    That difference is a way of asking who has an account here — the same disclosure
    the shared error message exists to prevent.
    """
    for _ in range(lockout.FREE_ATTEMPTS + 1):
        await signed_up.post(
            "/api/auth/login", json={"username": "nobody-at-all", "password": WRONG}
        )

    response = await signed_up.post(
        "/api/auth/login", json={"username": "nobody-at-all", "password": WRONG}
    )

    assert response.status_code == 429


async def test_the_account_and_the_address_are_counted_separately(
    migrated: AsyncSession,
) -> None:
    """Two different attacks need two different counters.

    Guessing one account's password is caught by the account counter. Trying one common
    password against many accounts is not — each account sees a single failure — and is
    caught by the address counter instead.
    """
    for _ in range(lockout.FREE_ATTEMPTS + 1):
        await lockout.record_failure(
            migrated, action="login", username="somebody", ip="203.0.113.9"
        )

    scopes = {row.scope for row in (await migrated.execute(select(AuthAttempt))).scalars()}
    assert scopes == {"account", "ip"}


async def test_a_long_quiet_period_starts_the_count_again(migrated: AsyncSession) -> None:
    """Four typos spread over a year are not an attack."""
    for _ in range(lockout.FREE_ATTEMPTS):
        await lockout.record_failure(
            migrated, action="login", username=USERNAME, ip="203.0.113.9"
        )

    for row in (await migrated.execute(select(AuthAttempt))).scalars():
        row.last_failed_at = dt.datetime.now(dt.UTC) - lockout.COUNTER_TTL * 2
    await migrated.commit()

    lock = await lockout.record_failure(
        migrated, action="login", username=USERNAME, ip="203.0.113.9"
    )

    assert lock is None
    row = await migrated.scalar(select(AuthAttempt).where(AuthAttempt.scope == "account"))
    assert row is not None
    assert row.failures == 1


async def test_actions_are_throttled_separately(migrated: AsyncSession) -> None:
    """Exhausting one route must not lock a person out of the one that would recover it."""
    for _ in range(lockout.FREE_ATTEMPTS + 1):
        await lockout.record_failure(migrated, action="login", username=USERNAME, ip=None)

    assert await lockout.check(migrated, action="login", username=USERNAME, ip=None)
    assert await lockout.check(migrated, action="forgot", username=USERNAME, ip=None) is None


async def test_stale_counters_are_cleaned_up(migrated: AsyncSession) -> None:
    await lockout.record_failure(migrated, action="login", username=USERNAME, ip=None)
    for row in (await migrated.execute(select(AuthAttempt))).scalars():
        row.last_failed_at = dt.datetime.now(dt.UTC) - lockout.COUNTER_TTL * 2
    await migrated.commit()

    deleted = await lockout.delete_stale_counters(migrated)

    assert deleted == 1
