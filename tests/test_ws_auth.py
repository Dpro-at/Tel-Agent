"""WebSocket authentication — D15.

The acceptance conditions, both asserted against a probe route since the product's
websocket routes arrive with the live view: an unauthenticated upgrade is refused, and
deleting the session closes an already-open socket.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from api.config import Settings
from api.main import create_app
from api.models import Session
from api.security.session import COOKIE_NAME
from api.security.websocket import watch_session
from api.setup import create_first_administrator

USERNAME = "wagner"
PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def ws_setup(migrated: AsyncSession, settings: Settings, database_url: str):
    # Async so that `migrated` actually resolves - a sync fixture requesting an async
    # one receives nothing and the administrator is silently never created. The body
    # still drives everything through TestClient's own loop; blocking pytest's loop
    # while it does is harmless in a test.
    """An app with a probe websocket route, a signed-in token, and one client.

    Everything - lifespan, login, websockets - runs through the same `TestClient`, on
    purpose. The client hosts the app on its own event loop, and asyncpg refuses to
    use a connection from any loop but the one that created it: an app whose engine
    was opened on pytest's loop works on SQLite and breaks on PostgreSQL, which is
    precisely the class of bug the dual-dialect suite exists to catch.
    """
    await create_first_administrator(
        migrated, username=USERNAME, password=PASSWORD, workspace_name="Wagner & Partner"
    )

    app = create_app(settings.model_copy(update={"database_url": database_url}))

    @app.websocket("/ws-probe")
    async def probe(websocket: WebSocket) -> None:
        await websocket.accept()
        # The route runs the watchdog beside its own loop, exactly as a real route
        # will. A tight interval keeps the test fast; the value is behaviourally
        # identical to the five-second default.
        token = websocket.scope["state"]["session_token"]
        watchdog = asyncio.create_task(
            watch_session(websocket, websocket.app.state.sessionmaker, token, interval=0.1)
        )
        try:
            await websocket.send_text("connected")
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            watchdog.cancel()

    with TestClient(app, base_url="http://localhost") as client:
        response = client.post(
            "/api/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
            headers={"Host": "localhost"},
        )
        assert response.status_code == 200
        yield (
            app,
            response.cookies[COOKIE_NAME],
            client,
            settings.model_copy(update={"database_url": database_url}),
        )


def test_an_unauthenticated_upgrade_is_refused(ws_setup) -> None:
    """No cookie, no socket - refused at the handshake, before accept."""
    _app, _token, client, _settings = ws_setup
    client.cookies.clear()

    with pytest.raises(WebSocketDisconnect) as refusal:
        with client.websocket_connect("/ws-probe", headers={"Host": "localhost"}):
            pass  # pragma: no cover - the handshake must not get this far

    assert refusal.value.code == 1008


def test_a_forged_cookie_is_refused(ws_setup) -> None:
    _app, _token, client, _settings = ws_setup
    client.cookies.clear()

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws-probe",
            headers={
                "Host": "localhost",
                "Cookie": f"{COOKIE_NAME}=a-token-nobody-issued",
            },
        ):
            pass  # pragma: no cover


def test_a_session_opens_the_socket(ws_setup) -> None:
    _app, token, client, _settings = ws_setup

    with client.websocket_connect(
        "/ws-probe",
        headers={"Host": "localhost", "Cookie": f"{COOKIE_NAME}={token}"},
    ) as websocket:
        assert websocket.receive_text() == "connected"


def test_deleting_the_session_closes_the_open_socket(ws_setup) -> None:
    """D15's second condition: the forgotten tablet's live view goes dark now, not at
    its next message - a listener may never send one."""
    _app, token, client, ws_settings = ws_setup

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(
            "/ws-probe",
            headers={"Host": "localhost", "Cookie": f"{COOKIE_NAME}={token}"},
        ) as websocket:
            assert websocket.receive_text() == "connected"

            # Revoke the session out from under the open socket. A fresh engine on
            # this thread's own loop, because borrowing the app's engine from a
            # foreign loop is exactly what asyncpg refuses.
            async def revoke() -> None:
                from api.db import create_engine

                engine = create_engine(ws_settings)
                try:
                    async with engine.begin() as connection:
                        await connection.execute(delete(Session))
                finally:
                    await engine.dispose()

            asyncio.run(revoke())

            # The watchdog polls every 0.1s; the next receive observes the close.
            websocket.receive_text()

    assert closed.value.code == 1008
