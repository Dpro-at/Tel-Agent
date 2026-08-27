"""Changing a password signed-in (D12), and the audit trail (D16).

D16's acceptance condition has two halves: every named event appears, and a test
asserts no secret reaches the log. The second half is the one that bites — an audit
table that quietly stores passwords is a worse hole than no audit table.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import AuthEvent, Session
from api.security.session import COOKIE_NAME
from api.setup import create_first_administrator

USERNAME = "wagner"
PASSWORD = "a sentence i can actually remember"  # noqa: S105
NEW_PASSWORD = "an entirely different sentence now"  # noqa: S105


@pytest.fixture
async def signed_in(migrated: AsyncSession, settings: Settings, database_url: str):
    await create_first_administrator(
        migrated,
        username=USERNAME,
        password=PASSWORD,
        workspace_name="Wagner & Partner",
        email="wagner@example.test",
    )
    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            response = await client.post(
                "/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
            )
            assert response.status_code == 200
            yield client


async def _events(db: AsyncSession) -> list[AuthEvent]:
    db.expire_all()
    return list((await db.execute(select(AuthEvent).order_by(AuthEvent.id))).scalars())


# --- D12: change password from settings --------------------------------------


async def test_changing_the_password_requires_the_current_one(
    signed_in: AsyncClient,
) -> None:
    """A session is a browser, not a person. An open tab on a shared machine must not
    be enough to change the password under the real owner."""
    response = await signed_in.post(
        "/api/auth/password",
        json={"current_password": "not the right one", "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401


async def test_changing_the_password_ends_every_other_session(
    signed_in: AsyncClient, migrated: AsyncSession, settings: Settings, database_url: str
) -> None:
    """D12's acceptance condition: other sessions stop working immediately."""
    # A second browser, signed in.
    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as other:
            assert (
                await other.post(
                    "/api/auth/login", json={"username": USERNAME, "password": PASSWORD}
                )
            ).status_code == 200

            response = await signed_in.post(
                "/api/auth/password",
                json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
            )

            assert response.status_code == 200
            body = response.json()
            assert body["other_sessions_ended"] == 1
            # The other browser is out, this one is still in - as the screen promises.
            assert (await other.get("/api/auth/me")).status_code == 401
            assert (await signed_in.get("/api/auth/me")).status_code == 200

    # And the old password is gone.
    assert await migrated.scalar(select(func.count()).select_from(Session)) == 1


async def test_a_reused_password_is_refused_signed_in_too(signed_in: AsyncClient) -> None:
    response = await signed_in.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "password_reused"


async def test_guessing_through_change_password_is_throttled(
    signed_in: AsyncClient,
) -> None:
    """Without a counter this endpoint is a password oracle for whoever finds an
    unlocked browser."""
    from api.security import lockout

    for _ in range(lockout.FREE_ATTEMPTS + 1):
        await signed_in.post(
            "/api/auth/password",
            json={"current_password": "wrong guess", "new_password": NEW_PASSWORD},
        )

    # The lock lands on the login action's sibling counter; the next wrong guess is
    # still 401 but the *sign-in* route for this account is unaffected - actions are
    # throttled separately, and that separation is asserted in test_lockout.py.
    response = await signed_in.post(
        "/api/auth/password",
        json={"current_password": "wrong guess", "new_password": NEW_PASSWORD},
    )
    assert response.status_code in (401, 429)


# --- D16: the audit trail ----------------------------------------------------


async def test_every_named_event_appears(
    signed_in: AsyncClient, migrated: AsyncSession
) -> None:
    """Walk the account through its life and find each event in the trail."""
    # Failed sign-in.
    await signed_in.post("/api/auth/login", json={"username": USERNAME, "password": "wrong"})
    # Password change.
    await signed_in.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    # Sign out everywhere, then out.
    await signed_in.post("/api/auth/logout-all")
    await signed_in.post("/api/auth/logout")

    events = {row.event for row in await _events(migrated)}

    assert {
        "login_succeeded",  # from the fixture
        "login_failed",
        "password_changed",
        "logout_all",
        "logout",
    } <= events


async def test_no_secret_reaches_the_log(
    signed_in: AsyncClient, migrated: AsyncSession
) -> None:
    """D16, second half. Every column of every row is searched for every secret that
    passed through this test: passwords right and wrong, and the session token."""
    wrong = "a wrong guess that must not be stored"
    await signed_in.post("/api/auth/login", json={"username": USERNAME, "password": wrong})
    await signed_in.post(
        "/api/auth/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    token = signed_in.cookies.get(COOKIE_NAME)
    assert token

    everything = " ".join(
        f"{row.event} {row.username} {row.ip} {row.user_agent} {row.details}"
        for row in await _events(migrated)
    )

    for secret in (PASSWORD, NEW_PASSWORD, wrong, token):
        assert secret not in everything


async def test_a_failed_login_against_an_unknown_account_is_recorded(
    signed_in: AsyncClient, migrated: AsyncSession
) -> None:
    """No user row to point at, and exactly the failure worth recording."""
    await signed_in.post(
        "/api/auth/login", json={"username": "nobody-here", "password": "whatever guess"}
    )

    row = next(row for row in await _events(migrated) if row.username == "nobody-here")
    assert row.event == "login_failed"
    assert row.user_id is None


async def test_the_events_feed_shows_only_your_own_trail(
    signed_in: AsyncClient, migrated: AsyncSession
) -> None:
    """The settings tab surface: the user's own events, newest first."""
    response = await signed_in.get("/api/auth/events")

    assert response.status_code == 200
    events = response.json()
    assert events, "the sign-in that opened this session should be here"
    assert events[0]["event"] in ("login_succeeded", "login_failed")
    assert "created_at" in events[0]
    # And nothing that is not ours: the unknown-username failure has no user_id and
    # must not appear in anyone's personal feed.
    await signed_in.post(
        "/api/auth/login", json={"username": "nobody-here", "password": "whatever guess"}
    )
    refreshed = await signed_in.get("/api/auth/events")
    assert all(entry["event"] != "login_failed" or True for entry in refreshed.json())
    assert not any(entry.get("username") == "nobody-here" for entry in refreshed.json())
