"""Shared fixtures.

The first test is expensive and every one after it is cheap. This file is the expensive
part, written before there are twenty endpoints to test at once.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from agent.config import llm_settings
from alembic import command
from api.config import Settings, get_settings
from api.db import create_engine, create_sessionmaker, session_scope
from api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No test reads the developer's real `.env`, and none leaks settings into another.

    `get_settings` is cached for the life of the process, which is right in production
    and wrong in a test session: without clearing it, the first test to build settings
    would decide the configuration for every test after it.

    This is what makes the promise on the `settings` fixture below true for the whole
    suite rather than only for tests that use it.
    """
    # The `.env` *file*, not only the variables. `get_settings()` builds `Settings()`
    # with `env_file` pointing at the repository root, so a developer who has one is
    # running a different configuration from CI - and the divergence is invisible,
    # because the failures land in tests about a key being *absent*. Clearing the
    # variables alone is not enough: `api.security.crypto.key_available` and the
    # encrypted column both ask `get_settings()` rather than the settings the app under
    # test was built with, so a real `ENCRYPTION_KEY` in the file makes an application
    # constructed with `encryption_key=None` encrypt anyway.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in ("ENVIRONMENT", "LOG_LEVEL", "DATABASE_URL", "CORS_ORIGINS", "ENCRYPTION_KEY"):
        monkeypatch.delenv(name, raising=False)
    # The model too, and for a second reason: a contributor with a real key in their
    # environment would otherwise have the suite call it, at their expense, on every
    # test that streams a reply.
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    llm_settings.cache_clear()
    yield
    get_settings.cache_clear()
    llm_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings built from defaults, with the `.env` file deliberately ignored.

    A contributor with a populated `.env` must get the same result from the suite as
    CI does on a clean checkout.
    """
    #
    # **The scheduler is off.** Every application built in this suite starts the job
    # loop, and its first tick runs immediately - so every test that builds an app also
    # runs the scheduler against that test's database, for work no test is asking
    # about. It costs about a fifth of the suite's time, and on PostgreSQL it is
    # background work holding a connection while the next test tries to drop the
    # schema. `tests/test_jobs.py` drives the runner directly, which is the honest way
    # to test a scheduler anyway.
    return Settings(_env_file=None, jobs_enabled=False)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client speaking to the application in-process.

    `ASGITransport` means no port is bound and no server is started, so the suite stays
    fast and cannot collide with a development server already running on 8000.
    """
    app = create_app(settings)

    # `ASGITransport` does not run startup or shutdown events. Without this the engine
    # is never opened, `/health` reports a database it never had, and every resource
    # opened in `lifespan` goes untested for as long as the suite exists.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://localhost",
        ) as async_client:
            yield async_client


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A client against an application whose lifespan has actually run.

    Used by tests that need a *differently configured* application than the shared
    `client` fixture provides - a different database URL, or an extra route.
    """
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client


# D-029 says both dialects are supported. That is an assertion until the suite has run
# against both, so the whole suite runs twice: once on SQLite, and once on PostgreSQL
# when `TEST_POSTGRES_URL` points at one. CI sets it; a contributor without a server
# gets the SQLite half and a skip rather than a failure.
POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


async def _create_database(url: str) -> None:
    """Make this database if it is not there, from the maintenance database.

    `CREATE DATABASE` cannot run inside a transaction and cannot be issued from within
    the database it names, so it goes through `postgres` with autocommit.
    """
    name = url.rsplit("/", 1)[1]
    server = url.rsplit("/", 1)[0] + "/postgres"
    engine = create_engine(Settings(_env_file=None, database_url=server)).execution_options(
        isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                sa_text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                await connection.execute(sa_text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def _worker_database(url: str) -> str:
    """One PostgreSQL database per parallel worker.

    Without this the suite cannot be run in parallel at all: `_reset_postgres` drops and
    recreates `public`, so two workers sharing a database would delete each other's
    schema mid-test - and the failures would look like flaky tests rather than like the
    configuration mistake they are.

    `PYTEST_XDIST_WORKER` is `gw0`, `gw1`, … under `-n`, and unset otherwise, so a plain
    serial run keeps using the database it always did.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return url
    base, name = url.rsplit("/", 1)
    return f"{base}/{name}_{worker}"


async def _open_sessions(url: str) -> str:
    """Every other connection to this database, and what it is doing.

    Read only when the reset below has already failed. A `DROP SCHEMA` that cannot get
    its lock is never the fault of the test that ran into it - it is the fault of the
    one before, which finished without closing something. This is the line that names
    it.
    """
    engine = create_engine(Settings(_env_file=None, database_url=url))
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                sa_text(
                    "SELECT pid, state, wait_event_type, wait_event, state_change, query "
                    "FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )
            return "\n".join(f"  {row}" for row in rows) or "  (none)"
    except Exception as reading_failed:  # pragma: no cover - diagnostics only
        return f"  (could not be read: {reading_failed!r})"
    finally:
        await engine.dispose()


async def _reset_postgres(url: str) -> None:
    """Give each test an empty schema.

    Dropping and recreating `public` rather than deleting rows: a migration test has to
    start from nothing, and a leftover table from a previous test would make the next
    one pass for the wrong reason.

    **`lock_timeout` is what keeps a leak from reading as an infinite test.** `DROP
    SCHEMA` waits for its lock with no deadline, so one connection left open by an
    earlier test stops the suite dead - and CI cancels the job twenty minutes later
    having printed nothing about where it stopped. That happened on three consecutive
    runs. Ten seconds is orders of magnitude longer than the drop needs when the
    schema is free, so this only fires on a real leak, and when it does it says which
    connection is still holding on.
    """
    engine = create_engine(Settings(_env_file=None, database_url=url))
    try:
        async with engine.begin() as connection:
            await connection.execute(sa_text("SET lock_timeout = '10s'"))
            await connection.execute(sa_text("DROP SCHEMA public CASCADE"))
            await connection.execute(sa_text("CREATE SCHEMA public"))
    except DBAPIError as blocked:
        still_connected = await _open_sessions(url)
        raise RuntimeError(
            "Could not reset the PostgreSQL schema - a connection from an earlier test "
            f"is still holding it.\nStill connected:\n{still_connected}"
        ) from blocked
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
async def postgres_worker_database() -> None:
    """Create this worker's own database, once, before anything asks for it."""
    if POSTGRES_URL is not None:
        await _create_database(_worker_database(POSTGRES_URL))


@pytest.fixture(
    params=["sqlite", *(["postgresql"] if POSTGRES_URL else [])],
    ids=lambda dialect: f"on-{dialect}",
)
def database_url(
    request: pytest.FixtureRequest, tmp_path: Path, postgres_worker_database: None
) -> str:
    """The URL under test, one per supported dialect."""
    if request.param == "postgresql":
        assert POSTGRES_URL is not None
        return _worker_database(POSTGRES_URL)
    return f"sqlite+aiosqlite:///{tmp_path}/test.db"


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return config


async def upgrade_to_head(database_url: str) -> None:
    """`alembic upgrade head` against this URL, the way an operator would run it."""
    config = _alembic_config()
    # env.py reads the URL from Settings, so it is set the way an operator would.
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    try:
        # `alembic/env.py` calls `asyncio.run()`, which refuses to start inside an
        # already-running loop. Running it in a worker thread is not a workaround for
        # the test: it is what happens in reality, where `alembic upgrade head` is its
        # own process with its own loop.
        await asyncio.to_thread(command.upgrade, config, "head")
    finally:
        os.environ.pop("DATABASE_URL", None)
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def sqlite_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The migrated SQLite schema, built once and copied per test.

    **Five hundred and forty-six of this suite's tests were each running the whole
    migration chain from nothing** — about 830 ms apiece, roughly two thirds of an
    eleven-minute run, to arrive at the identical empty schema every time. Copying the
    file instead costs about a millisecond.

    **This does not weaken what the fixture promises.** The schema still comes from
    *running* the migration rather than from `Base.metadata.create_all()`; the migration
    simply runs once per session instead of once per test, and every test gets a copy of
    its output. A copy of the migration's result is not the models testing themselves.

    A test that needs the chain itself — `tests/test_positions.py` upgrades to an
    earlier revision and then forward — drives alembic directly and is unaffected.
    """
    path = tmp_path_factory.mktemp("schema") / "template.db"
    asyncio.run(upgrade_to_head(f"sqlite+aiosqlite:///{path}"))
    return path


@pytest.fixture
async def migrated(
    settings: Settings, database_url: str, sqlite_template: Path
) -> AsyncIterator[AsyncSession]:
    """A database built by `alembic upgrade head`.

    The schema comes from **running the migration**, not from
    `Base.metadata.create_all()`. Creating tables from the models would test the models
    against themselves and pass even if the migration were empty - and the migration is
    what actually reaches an installation. See `sqlite_template` for why that run is
    once per session rather than once per test.
    """
    if database_url.startswith("postgresql"):
        # **No template on this side, and that is a measurement rather than an
        # oversight.** The same trick was tried here - `CREATE DATABASE … TEMPLATE` in
        # place of the migration - and it made the PostgreSQL half *slower*: 37.8s to
        # 50.7s over the same fifty-one tests. Cloning a database copies its whole file
        # layout and needs every connection dropped first, which costs more than
        # emptying a small schema and replaying the chain on a connection that is
        # already open. SQLite has no equivalent cost, which is why it keeps the copy.
        await _reset_postgres(database_url)
        await upgrade_to_head(database_url)
    else:
        # `database_url` is `sqlite+aiosqlite:///<tmp_path>/test.db`, and the file does
        # not exist yet - this is what creates it.
        shutil.copyfile(sqlite_template, database_url.split("///", 1)[1])

    engine = create_engine(settings.model_copy(update={"database_url": database_url}))
    sessionmaker = create_sessionmaker(engine)
    try:
        async with session_scope(sessionmaker) as session:
            yield session
    finally:
        await engine.dispose()
