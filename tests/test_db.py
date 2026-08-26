"""The engine, the session lifecycle, and the deep health check.

B9's acceptance condition is that `/health` reports database reachability and that
sessions are returned to the pool. A leaked connection is the failure that only appears
under load, so it is asserted rather than assumed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from api.config import Settings
from api.db import (
    NAMING_CONVENTION,
    Base,
    check_database,
    create_engine,
    create_sessionmaker,
    session_scope,
)
from tests.conftest import running_app


@pytest.fixture
async def engine(tmp_path: pytest.TempPathFactory, settings: Settings) -> AsyncEngine:
    """A throwaway database per test, on disk rather than in memory.

    In-memory SQLite gives each pooled connection its own empty database, which hides
    exactly the pooling behaviour these tests exist to check.
    """
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"  # type: ignore[str-format]
    engine = create_engine(settings.model_copy(update={"database_url": url}))
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_a_session_is_returned_to_the_pool(engine: AsyncEngine) -> None:
    """Borrow, use, release — and the pool ends where it started."""
    sessionmaker = create_sessionmaker(engine)

    async with session_scope(sessionmaker) as session:
        await session.execute(text("SELECT 1"))

    assert engine.pool.checkedout() == 0


async def test_a_session_is_returned_even_when_the_request_fails(
    engine: AsyncEngine,
) -> None:
    """The important half. A handler that raises must not keep the connection.

    `async with` throws the exception back in at the `yield`, so the rollback and the
    close both run. An `async for` loop does not, which is exactly the bug this suite
    caught in `get_session`: one connection leaked per failed request.
    """
    sessionmaker = create_sessionmaker(engine)

    with pytest.raises(RuntimeError):
        async with session_scope(sessionmaker) as session:
            await session.execute(text("SELECT 1"))
            raise RuntimeError("handler blew up")

    assert engine.pool.checkedout() == 0


async def test_a_failed_transaction_is_rolled_back(engine: AsyncEngine) -> None:
    """A connection must never go back to the pool inside a broken transaction.

    The next request to borrow it would inherit the failure, and the bug would appear
    to belong to whoever was unlucky rather than to whoever caused it.
    """
    sessionmaker = create_sessionmaker(engine)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY)"))

    with pytest.raises(RuntimeError):
        async with session_scope(sessionmaker) as session:
            await session.execute(text("INSERT INTO t (id) VALUES (1)"))
            raise RuntimeError("failed after writing")

    async with session_scope(sessionmaker) as session:
        rows = (await session.execute(text("SELECT COUNT(*) FROM t"))).scalar_one()

    assert rows == 0


async def test_sqlite_enforces_foreign_keys(engine: AsyncEngine) -> None:
    """SQLite ignores foreign keys unless told otherwise, per connection.

    Without the pragma in `api/db.py`, `memberships.user_id` could point at a user that
    does not exist and the constraint would be a comment.
    """
    from sqlalchemy.exc import IntegrityError

    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE parent (id INTEGER PRIMARY KEY)"))
        await connection.execute(
            text(
                "CREATE TABLE child ("
                "  id INTEGER PRIMARY KEY,"
                "  parent_id INTEGER NOT NULL REFERENCES parent(id)"
                ")"
            )
        )

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO child (id, parent_id) VALUES (1, 999)"))


async def test_check_database_is_true_when_reachable(engine: AsyncEngine) -> None:
    assert await check_database(engine) is True


async def test_check_database_is_false_when_the_engine_is_disposed(
    settings: Settings, tmp_path: pytest.TempPathFactory
) -> None:
    """A dead dependency must report as dead, not raise into the health endpoint."""
    url = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/nothing"
    unreachable = create_engine(settings.model_copy(update={"database_url": url}))
    try:
        assert await check_database(unreachable) is False
    finally:
        await unreachable.dispose()


def test_the_naming_convention_covers_every_constraint_type() -> None:
    """Alembic can only drop a constraint it can name, and SQLite rebuilds tables."""
    assert set(NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}
    assert Base.metadata.naming_convention == NAMING_CONVENTION


def test_pool_sizing_applies_to_postgres_and_not_to_sqlite(settings: Settings) -> None:
    """Pooling is meaningful only where there is a server on the other end.

    A distinctive size is used deliberately: the SQLAlchemy default is 5 and so is
    ours, so asserting against the default would pass whether or not the setting was
    ever passed through.
    """
    configured = settings.model_copy(update={"database_pool_size": 17})

    postgres = create_engine(
        configured.model_copy(
            update={"database_url": "postgresql+asyncpg://u:p@localhost/telagent"}
        )
    )
    sqlite = create_engine(configured)

    assert postgres.pool.size() == 17
    assert sqlite.dialect.name == "sqlite"
    assert sqlite.pool.size() != 17


async def test_health_reports_the_database(settings: Settings) -> None:
    """The deep check §B8 asks for: it reports what it actually verified."""
    async with running_app(
        settings.model_copy(update={"database_url": "sqlite+aiosqlite://"})
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] is True


async def test_health_is_503_when_the_database_is_unreachable(settings: Settings) -> None:
    """A silently dead service is worse than an obviously dead one.

    503 rather than 200-with-a-flag, so a load balancer acts on it without parsing the
    body — while the body still names which dependency failed.
    """
    url = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/nothing"
    async with running_app(settings.model_copy(update={"database_url": url})) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] is False
