"""The session dependency, driven by FastAPI rather than by hand.

`tests/test_db.py` checks `session_scope` in isolation. This checks the thing that
actually ships: a route that takes `get_session`, served through the whole middleware
stack, on a success and on a failure. The pool is inspected afterwards, because a
connection that never comes back is the failure that only shows up under load.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api import dependencies
from api.config import Settings
from api.main import SessionDep, create_app


@pytest.fixture
async def app_with_db_routes(
    settings: Settings, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
):
    """An application with one route that succeeds and one that fails, both using a session.

    Both are added to the public list for the duration of the test. Routes are closed by
    default (D5), so without this they would return 401 and this file would be testing
    the authentication gate instead of the connection pool it is about.
    """
    monkeypatch.setattr(
        dependencies,
        "PUBLIC_PATHS",
        dependencies.PUBLIC_PATHS | {"/query", "/query-then-fail"},
    )
    url = f"sqlite+aiosqlite:///{tmp_path}/routes.db"  # type: ignore[str-format]
    app = create_app(settings.model_copy(update={"database_url": url}))

    @app.get("/query")
    async def query(session: SessionDep) -> dict[str, int]:
        value = (await session.execute(text("SELECT 42"))).scalar_one()
        return {"value": value}

    @app.get("/query-then-fail")
    async def query_then_fail(session: SessionDep) -> None:
        await session.execute(text("SELECT 1"))
        raise RuntimeError("failed after using the session")

    async with app.router.lifespan_context(app):
        yield app


async def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://localhost",
    )


async def test_a_route_can_query_through_the_dependency(app_with_db_routes) -> None:
    async with await _client(app_with_db_routes) as client:
        response = await client.get("/query")

    assert response.status_code == 200
    assert response.json() == {"value": 42}
    assert app_with_db_routes.state.engine.pool.checkedout() == 0


async def test_the_connection_comes_back_when_the_route_raises(app_with_db_routes) -> None:
    """The 500 path. A handler that blows up must not take a connection with it."""
    async with await _client(app_with_db_routes) as client:
        response = await client.get("/query-then-fail")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    # The whole point of B9: no leak, even on the failing path.
    assert app_with_db_routes.state.engine.pool.checkedout() == 0


async def test_many_requests_do_not_exhaust_the_pool(app_with_db_routes) -> None:
    """Twenty requests against a pool of five. If sessions leak, this runs out."""
    async with await _client(app_with_db_routes) as client:
        for _ in range(20):
            assert (await client.get("/query")).status_code == 200

    assert app_with_db_routes.state.engine.pool.checkedout() == 0


async def test_the_engine_is_disposed_on_shutdown(
    settings: Settings, tmp_path: pytest.TempPathFactory
) -> None:
    """A reload in development must not leak the whole pool on every restart.

    A file rather than `:memory:`: in-memory SQLite uses `StaticPool`, which holds one
    connection forever and has no `checkedout()` to inspect.
    """
    url = f"sqlite+aiosqlite:///{tmp_path}/dispose.db"  # type: ignore[str-format]
    app = create_app(settings.model_copy(update={"database_url": url}))

    async with app.router.lifespan_context(app):
        engine = app.state.engine
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    # `dispose()` replaces the pool; nothing is left checked out behind it.
    assert engine.pool.checkedout() == 0
