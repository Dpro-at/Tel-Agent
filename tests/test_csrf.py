"""A cross-site POST with a valid cookie is refused, and the dashboard still works — D7.

That sentence is the acceptance condition, and both halves are asserted: the attack is
stopped *and* every legitimate caller keeps working. A CSRF layer that breaks the
dashboard gets turned off within the hour, which protects nobody.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from api.config import Settings
from api.main import create_app
from api.middleware.csrf import origin_allowed
from api.security.session import COOKIE_NAME
from api.setup import create_first_administrator

USERNAME = "wagner"
PASSWORD = "a sentence i can actually remember"  # noqa: S105

EVIL = "https://evil.example"


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


async def _sign_in(client: AsyncClient, origin: str | None = None) -> None:
    headers = {"Origin": origin} if origin else {}
    response = await client.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers=headers,
    )
    assert response.status_code == 200


# --- The attack is stopped ---------------------------------------------------


async def test_a_cross_site_post_with_a_valid_cookie_is_refused(
    signed_up: AsyncClient,
) -> None:
    """D7's acceptance condition, verbatim.

    The session is real and the cookie is attached — exactly what a browser does when
    a malicious page fires a request at this installation. The origin is what gives
    the attack away, and it is the one thing the attacking page cannot forge.
    """
    await _sign_in(signed_up)
    assert signed_up.cookies.get(COOKIE_NAME)

    response = await signed_up.post("/api/auth/logout", headers={"Origin": EVIL})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    # And the session survived: the attack changed nothing.
    assert (await signed_up.get("/api/auth/me")).status_code == 200


async def test_a_cross_site_login_attempt_is_refused_too(signed_up: AsyncClient) -> None:
    """Login CSRF: a page that signs the victim into the attacker's account, so that
    what the victim then does lands somewhere the attacker can read."""
    response = await signed_up.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"Origin": EVIL},
    )

    assert response.status_code == 403
    assert COOKIE_NAME not in response.cookies


async def test_an_origin_of_null_is_refused(signed_up: AsyncClient) -> None:
    """`Origin: null` is what sandboxed iframes send, and nothing legitimate here
    runs sandboxed."""
    response = await signed_up.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"Origin": "null"},
    )

    assert response.status_code == 403


async def test_a_lookalike_origin_prefix_is_refused(signed_up: AsyncClient) -> None:
    """`localhost.evil.example` contains the allowed host as a prefix; matching by
    substring instead of by whole host would wave it through."""
    response = await signed_up.post(
        "/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        headers={"Origin": "http://localhost.evil.example"},
    )

    assert response.status_code == 403


async def test_the_refusal_happens_before_authentication(signed_up: AsyncClient) -> None:
    """403, not 401: the cross-site verdict must not depend on whether the cookie was
    valid, or the response would tell the attacking page which victims are signed in."""
    signed_up.cookies.clear()

    response = await signed_up.post("/api/auth/logout", headers={"Origin": EVIL})

    assert response.status_code == 403


# --- The dashboard still works -----------------------------------------------


async def test_the_dashboard_origin_still_works(signed_up: AsyncClient) -> None:
    """The configured CORS origin — the dashboard in development — is allowed."""
    await _sign_in(signed_up, origin="http://localhost:38471")

    response = await signed_up.post(
        "/api/auth/logout", headers={"Origin": "http://localhost:38471"}
    )

    assert response.status_code == 200


async def test_the_installations_own_origin_still_works(signed_up: AsyncClient) -> None:
    """Same-origin, as production serves it: Origin matches the Host being called."""
    await _sign_in(signed_up, origin="http://localhost")

    response = await signed_up.post("/api/auth/logout", headers={"Origin": "http://localhost"})

    assert response.status_code == 200


async def test_a_request_with_no_origin_still_works(signed_up: AsyncClient) -> None:
    """curl, scripts, monitoring: no browser steered them, so there is no CSRF to
    stop. Refusing them would break §B6's public API without protecting anything."""
    await _sign_in(signed_up)

    response = await signed_up.post("/api/auth/logout")

    assert response.status_code == 200


async def test_safe_methods_are_not_origin_checked(signed_up: AsyncClient) -> None:
    """A GET changes nothing, and CORS already governs whether a foreign page can
    read the answer. Blocking reads here would duplicate CORS badly."""
    response = await signed_up.get("/health", headers={"Origin": EVIL})

    assert response.status_code == 200


async def test_gate_refusals_carry_cors_headers(signed_up: AsyncClient) -> None:
    """A 401 from the authentication gate must still be readable by the dashboard.

    The regression that motivates this: with CORS registered inside the gates, their
    refusals were born without `Access-Control-Allow-Origin`, and the browser reported
    them as network failures - so the dashboard could never read the 401 that should
    send somebody to the sign-in screen. curl does not enforce CORS, which is why only
    a browser ever saw it, and why this asserts the header rather than the status.
    """
    signed_up.cookies.clear()
    origin = "http://localhost:38471"

    unauthenticated = await signed_up.get("/api/auth/me", headers={"Origin": origin})
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers.get("access-control-allow-origin") == origin

    forged = await signed_up.post("/api/auth/logout", headers={"Origin": origin})
    # Allowed origin, no session: this is the auth gate again, through CORS.
    assert forged.status_code == 401
    assert forged.headers.get("access-control-allow-origin") == origin


# --- The origin rule itself --------------------------------------------------


@pytest.mark.parametrize(
    ("origin", "host", "allowed"),
    [
        # The reverse-proxy case: browser says https, TLS terminated in front.
        ("https://telagent.local", "telagent.local", True),
        # Same host, explicit port on both sides.
        ("http://192.168.1.10:8000", "192.168.1.10:8000", True),
        # Port differs: a different origin in every way that matters.
        ("http://telagent.local:9999", "telagent.local", False),
        # Case-insensitive, as host names are.
        ("https://TelAgent.LOCAL", "telagent.local", True),
        ("https://evil.example", "telagent.local", False),
        ("null", "telagent.local", False),
        ("not a url at all", "telagent.local", False),
    ],
)
def test_same_host_comparison(origin: str, host: str, allowed: bool) -> None:
    assert origin_allowed(origin, request_host=host, allowed_origins=[]) is allowed


# --- WebSocket handshakes ----------------------------------------------------
#
# The spec's hardest sentence: "applies to WebSocket upgrades too — they carry cookies
# and are not covered by CORS." There is no websocket route in the product yet (D15),
# so a probe route stands in — the middleware is what is under test, and it must
# already cover the scope type before the first real route lands.


@pytest.fixture
def ws_app(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    # The probe is public for the duration: this file tests the CSRF gate, and since
    # D15 the authentication gate would otherwise refuse the handshake first - the
    # foreign-origin test would then pass for the wrong reason.
    from api import dependencies

    monkeypatch.setattr(dependencies, "PUBLIC_PATHS", dependencies.PUBLIC_PATHS | {"/ws-probe"})
    app = create_app(settings)

    @app.websocket("/ws-probe")
    async def probe(websocket: WebSocket) -> None:
        # The annotation is load-bearing: FastAPI injects by type, and an untyped
        # parameter here is read as a required query field instead of the socket.
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    return app


def test_a_websocket_handshake_from_a_foreign_origin_is_refused(ws_app) -> None:
    client = TestClient(ws_app)

    # Host passed explicitly: websocket_connect does not inherit it from base_url, and
    # without it TrustedHost refuses first - the test would then pass for the wrong
    # reason, proving nothing about CSRF.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws-probe", headers={"Origin": EVIL, "Host": "localhost"}
        ):
            pass  # pragma: no cover - the handshake must not get this far


def test_a_websocket_handshake_from_the_dashboard_completes(ws_app) -> None:
    client = TestClient(ws_app)

    with client.websocket_connect(
        "/ws-probe", headers={"Origin": "http://localhost:38471", "Host": "localhost"}
    ) as websocket:
        assert websocket.receive_text() == "hello"


def test_nothing_exempt_from_csrf_may_require_a_session() -> None:
    """The invariant that makes `CSRF_EXEMPT_PREFIXES` safe rather than a hole.

    CSRF exists because the browser attaches the session cookie to a request another
    site triggered. A route with no session has nothing to attach and nothing to forge,
    which is why `/public/` is exempt.

    A route that is exempt *and* authenticated would be the one combination the
    middleware exists to prevent - so the exemption is checked against the list of
    session-less paths rather than trusted to stay true.
    """
    from api.dependencies import PUBLIC_PREFIXES
    from api.middleware.csrf import CSRF_EXEMPT_PREFIXES

    for exempt in CSRF_EXEMPT_PREFIXES:
        assert any(
            exempt.startswith(public) or public.startswith(exempt) for public in PUBLIC_PREFIXES
        ), f"{exempt} is exempt from CSRF but is not a public, session-less prefix"
