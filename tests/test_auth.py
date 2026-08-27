"""Signing in, staying in, and getting out — D4, D5 and D6.

The acceptance conditions, asserted:

* correct credentials return a session cookie, and wrong ones return the same message
  either way (D4);
* a route without a session is unreachable, proved by walking the route table (D5);
* a logged-out cookie is refused on the next request, and sign-out-everywhere ends the
  others while keeping this one (D6).
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.dependencies import PUBLIC_PATHS, served_paths
from api.main import create_app
from api.models import Session
from api.security.session import COOKIE_NAME, SESSION_LIFETIME, hash_token
from api.setup import create_first_administrator

USERNAME = "wagner"
PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def signed_up(migrated: AsyncSession, settings: Settings, database_url: str):
    """An installation with one administrator, and a client against it."""
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
            yield client


async def _login(
    client: AsyncClient, *, password: str | None = None, username: str | None = None
):
    """Sign in, defaulting to the administrator this fixture created.

    The credentials are resolved inside rather than given as default arguments: a
    password in a signature is a pattern worth never establishing, even in a test file,
    because the next person copies the shape and not the context.
    """
    return await client.post(
        "/api/auth/login",
        json={"username": username or USERNAME, "password": password or PASSWORD},
    )


# --- D4: login ---------------------------------------------------------------


async def test_correct_credentials_return_a_session_cookie(signed_up: AsyncClient) -> None:
    response = await _login(signed_up)

    assert response.status_code == 200
    assert response.cookies[COOKIE_NAME]
    body = response.json()
    assert body["username"] == USERNAME
    # The workspace created at first run comes back with it, so the dashboard knows
    # what it is looking at without a second request.
    assert [w["role"] for w in body["workspaces"]] == ["owner"]


async def test_the_cookie_is_httponly_and_samesite_lax(signed_up: AsyncClient) -> None:
    """`HttpOnly` so an XSS bug cannot read it; `Lax` so it is not sent cross-site."""
    response = await _login(signed_up)

    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert f"max-age={int(SESSION_LIFETIME.total_seconds())}" in header
    # Not `Secure` in development: it would break http://localhost, and a developer who
    # cannot sign in turns the flag off everywhere.
    assert "secure" not in header


async def test_a_wrong_password_and_an_unknown_account_are_indistinguishable(
    signed_up: AsyncClient,
) -> None:
    """D4's actual requirement. Anything that differs turns the form into a directory."""
    wrong_password = await _login(
        signed_up,
        password="not the right one",  # noqa: S106 - wrong on purpose; that is the test
    )
    no_such_user = await _login(signed_up, username="nobody")

    assert wrong_password.status_code == no_such_user.status_code == 401
    assert wrong_password.json() == {
        **no_such_user.json(),
        "error": {
            **no_such_user.json()["error"],
            "request_id": wrong_password.json()["error"]["request_id"],
        },
    }
    assert COOKIE_NAME not in wrong_password.cookies


async def test_the_password_is_never_logged(
    signed_up: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    with caplog.at_level(logging.INFO):
        await _login(
            signed_up,
            password="hunter2-and-a-bit-more",  # noqa: S106 - looked for in the log below
        )

    logged = " ".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert "hunter2" not in logged


async def test_only_the_hash_of_the_token_is_stored(
    signed_up: AsyncClient, migrated: AsyncSession
) -> None:
    """A stolen database dump must contain no usable session."""
    response = await _login(signed_up)
    token = response.cookies[COOKIE_NAME]

    stored = await migrated.scalar(select(Session.token_hash))
    assert stored is not None
    assert stored != token
    assert stored == hash_token(token)


# --- D5: the current-user dependency, closed by default ----------------------


async def test_a_protected_route_needs_a_session(signed_up: AsyncClient) -> None:
    response = await signed_up.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_a_session_reaches_the_protected_route(signed_up: AsyncClient) -> None:
    await _login(signed_up)

    response = await signed_up.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["username"] == USERNAME


async def test_every_route_is_protected_unless_it_is_on_the_public_list(
    signed_up: AsyncClient, settings: Settings
) -> None:
    """D5's acceptance condition: walk the route table, prove nothing escaped.

    The proof is a request per route, not a set comparison. The previous version of
    this test built its candidate set with `if is_public(path)` and then asserted that
    set was a subset of `PUBLIC_PATHS` - which `is_public` guarantees by definition.
    It was a tautology, it passed on an empty route list, and it went on passing when
    a FastAPI change hid every real route behind `_IncludedRouter`. Asking the running
    application what it answers cannot be satisfied that way.
    """
    app = create_app(settings)
    served = served_paths(app)

    # The walk sees the real API, not just the four routes registered on the app
    # itself. If this fails, the walk is broken and everything below is vacuous.
    assert "/api/auth/login" in served
    assert "/api/auth/me" in served
    assert len(served) > 10

    protected = {
        path for path in served if path not in PUBLIC_PATHS and not path.startswith("/docs/")
    }
    assert protected, "no protected routes found - the walk is lying"

    # Signed out. The gate runs before routing, so the method does not matter: every
    # non-public path answers 401 whatever verb it was declared with.
    signed_up.cookies.clear()
    for path in sorted(protected):
        response = await signed_up.get(path)
        assert response.status_code == 401, f"{path} answered {response.status_code}"
        assert response.json()["error"]["code"] == "unauthenticated"

    # And the list itself is short enough to read in one go. If it is growing, that is
    # the thing to notice.
    assert len(PUBLIC_PATHS) <= 12


async def test_an_expired_session_is_refused_and_deleted(
    signed_up: AsyncClient, migrated: AsyncSession
) -> None:
    await _login(signed_up)
    session = await migrated.scalar(select(Session))
    assert session is not None

    session.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    await migrated.commit()

    response = await signed_up.get("/api/auth/me")

    assert response.status_code == 401
    # Not merely ignored: a row that can never authorise anything is deleted when noticed.
    assert await migrated.scalar(select(func.count()).select_from(Session)) == 0


async def test_a_forged_cookie_is_refused(signed_up: AsyncClient) -> None:
    signed_up.cookies.set(COOKIE_NAME, "a-token-nobody-issued")

    response = await signed_up.get("/api/auth/me")

    assert response.status_code == 401


async def test_a_signed_in_user_still_gets_404_for_a_real_typo(
    signed_up: AsyncClient,
) -> None:
    """The other half of hiding the route table: once inside, a wrong path is a wrong path."""
    await _login(signed_up)

    response = await signed_up.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_the_health_check_stays_public(signed_up: AsyncClient) -> None:
    """A monitor cannot sign in, and an unreachable health check is a dead monitor."""
    assert (await signed_up.get("/health")).status_code == 200


# --- D6: logout, and sign out everywhere -------------------------------------


async def test_logout_refuses_the_cookie_on_the_next_request(
    signed_up: AsyncClient, migrated: AsyncSession
) -> None:
    await _login(signed_up)

    logout = await signed_up.post("/api/auth/logout")

    assert logout.status_code == 200
    assert await migrated.scalar(select(func.count()).select_from(Session)) == 0
    # The row is gone, so even a copy of the cookie is now worthless.
    assert (await signed_up.get("/api/auth/me")).status_code == 401


async def test_logout_deletes_the_row_not_only_the_cookie(
    signed_up: AsyncClient, migrated: AsyncSession, settings: Settings, database_url: str
) -> None:
    """The shared-machine case: someone who kept the cookie value must lose access."""
    response = await _login(signed_up)
    stolen = response.cookies[COOKIE_NAME]

    await signed_up.post("/api/auth/logout")

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport, base_url="http://localhost", cookies={COOKIE_NAME: stolen}
        ) as thief:
            assert (await thief.get("/api/auth/me")).status_code == 401


async def test_sign_out_everywhere_keeps_this_session(
    signed_up: AsyncClient, migrated: AsyncSession, settings: Settings, database_url: str
) -> None:
    """Ends the forgotten tablet, not the browser doing the ending."""
    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as other:
            await _login(other)
            await _login(other)

    await _login(signed_up)
    assert await migrated.scalar(select(func.count()).select_from(Session)) == 3

    response = await signed_up.post("/api/auth/logout-all")

    assert response.status_code == 200
    assert response.json()["other_sessions_ended"] == 2
    assert await migrated.scalar(select(func.count()).select_from(Session)) == 1
    # This browser is still signed in.
    assert (await signed_up.get("/api/auth/me")).status_code == 200
