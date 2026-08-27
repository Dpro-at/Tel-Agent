"""The recent log behind the health screen — P6.

The Log panel has been drawn since the beginning with nothing behind it. This is what
fills it, and the tests that matter are the ones about what it must *not* do: leak a
secret, outgrow its bound, reach somebody who should not read it, or pretend to be a
durable record when it is a ring in memory.
"""

from __future__ import annotations

import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.logging import configure_logging, recent_log_handler
from api.main import create_app
from api.models import Membership, User, Workspace
from api.security.password import hash_password
from api.syslog import CAPACITY, Entry, RecentLogHandler

PASSWORD = "a sentence i can actually remember"  # noqa: S105
CREDENTIAL = "9999999999:AAH-telegram-bot-token"


# --- The ring itself ---------------------------------------------------------


def test_it_keeps_the_newest_and_drops_the_oldest() -> None:
    """Bounded so memory is a constant an operator can reason about, not a leak."""
    handler = RecentLogHandler(capacity=3)
    for index in range(5):
        handler.emit(
            logging.LogRecord("api.agent", logging.INFO, "x.py", 1, f"line {index}", (), None)
        )

    assert [entry.message for entry in handler.recent()] == ["line 4", "line 3", "line 2"]


def test_newest_first() -> None:
    """The panel is read top-down by somebody asking what just happened."""
    handler = RecentLogHandler()
    for message in ("first", "second"):
        handler.emit(logging.LogRecord("api.agent", logging.INFO, "x.py", 1, message, (), None))

    assert [entry.message for entry in handler.recent()] == ["second", "first"]


@pytest.mark.parametrize(
    ("logger_name", "service"),
    [
        ("api.access", "access"),
        ("api.security.session", "security"),
        ("agent.session", "session"),
        ("uvicorn.error", "uvicorn"),
    ],
)
def test_the_service_column_is_one_short_word(logger_name: str, service: str) -> None:
    """A dotted path in a narrow column pushes the message off the end - and the
    message is the part somebody is actually reading."""
    handler = RecentLogHandler()
    handler.emit(logging.LogRecord(logger_name, logging.INFO, "x.py", 1, "hello", (), None))

    assert handler.recent()[0].service == service


def test_a_handler_that_cannot_format_does_not_raise() -> None:
    """`emit` runs inside other people's error paths. Raising there would replace a
    real failure with a confusing one."""
    handler = RecentLogHandler()
    record = logging.LogRecord(
        "api.agent", logging.INFO, "x.py", 1, "%d items", ("not a number",), None
    )

    handler.emit(record)  # must not raise

    # Nothing usable was recorded, and that is the correct outcome: the incident this
    # ran inside of is what matters, not this line.
    assert len(handler.entries) <= 1


# --- The four filters the screen draws ---------------------------------------


def _fill(handler: RecentLogHandler) -> None:
    for name, level, message in (
        ("api.smtp", logging.WARNING, "connect refused"),
        ("api.tools", logging.ERROR, "send_notification timeout"),
        ("api.db", logging.INFO, "vacuum complete"),
        ("api.agent", logging.INFO, "call ended, 2:38"),
    ):
        handler.emit(logging.LogRecord(name, level, "x.py", 1, message, (), None))


def test_errors_shows_only_errors() -> None:
    handler = RecentLogHandler()
    _fill(handler)

    assert [e.service for e in handler.recent(level="errors")] == ["tools"]


def test_warnings_includes_errors() -> None:
    """The chip means "at least this serious". Showing warnings alone would hide the
    errors underneath them - the opposite of the question being asked."""
    handler = RecentLogHandler()
    _fill(handler)

    services = {e.service for e in handler.recent(level="warnings")}
    assert services == {"smtp", "tools"}


def test_calls_is_the_services_that_carry_a_conversation() -> None:
    """ "Calls" is not a level. It is agent, tools, sip, stt, tts."""
    handler = RecentLogHandler()
    _fill(handler)

    assert {e.service for e in handler.recent(level="calls")} == {"tools", "agent"}


# --- Wiring, and the secret that must not reach the screen -------------------


def test_configure_logging_installs_exactly_one_ring() -> None:
    """Called once per application built. Two rings would double every line."""
    configure_logging("INFO")
    configure_logging("INFO")

    root = logging.getLogger()
    rings = [h for h in root.handlers if isinstance(h, RecentLogHandler)]
    assert len(rings) == 1
    assert recent_log_handler() is rings[0]


def test_a_secret_never_reaches_the_ring() -> None:
    """The ring sits behind the same redaction filter as the JSON handler, so the panel
    cannot show something the log file would not have shown."""
    configure_logging("INFO")
    handler = recent_log_handler()
    assert handler is not None
    handler.clear()

    logging.getLogger("api.channels").warning("could not connect with token: %s", CREDENTIAL)

    everything = " ".join(entry.message for entry in handler.recent())
    assert CREDENTIAL not in everything
    assert "[redacted]" in everything


def test_the_request_id_travels_with_the_line() -> None:
    """A line on the screen has to be findable in the real log, and the id is how."""
    configure_logging("INFO")
    handler = recent_log_handler()
    assert handler is not None
    handler.clear()

    logging.getLogger("api.agent").info("something", extra={"request_id": "abc-123"})

    assert handler.recent()[0].request_id == "abc-123"


# --- The endpoint ------------------------------------------------------------


@pytest.fixture
async def clients(migrated: AsyncSession, settings: Settings, database_url: str):
    """An admin and a viewer in one workspace."""
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()

    password_hash = hash_password(PASSWORD)
    for username, role in (("mohamed", "admin"), ("lukas", "viewer")):
        user = User(username=username, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    opened: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "lukas"):
            client = AsyncClient(transport=transport, base_url="http://localhost")
            response = await client.post(
                "/api/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert response.status_code == 200
            opened[username] = client
        try:
            yield opened
        finally:
            for client in opened.values():
                await client.aclose()


async def test_an_admin_reads_the_log(clients) -> None:
    logging.getLogger("api.agent").error("something went wrong")

    response = await clients["mohamed"].get("/api/system/log?level=errors")

    assert response.status_code == 200
    body = response.json()
    assert body["capacity"] == CAPACITY
    assert any("something went wrong" in entry["message"] for entry in body["entries"])


async def test_a_viewer_cannot_read_the_log(clients) -> None:
    """Every other read here is workspace-scoped; a log line is not. It carries
    hostnames, paths and the shape of the internals, and the receptionist who may read
    every transcript has no reason to read the SMTP handshake."""
    response = await clients["lukas"].get("/api/system/log")

    assert response.status_code == 403
    assert "admin" in response.json()["error"]["message"]


async def test_signed_out_is_refused(clients) -> None:
    clients["mohamed"].cookies.clear()

    assert (await clients["mohamed"].get("/api/system/log")).status_code == 401


async def test_health_stays_public_and_carries_no_log(clients) -> None:
    """A monitor cannot sign in, so /health stays open - and therefore must not start
    carrying log lines with it."""
    clients["mohamed"].cookies.clear()

    response = await clients["mohamed"].get("/health")

    assert response.status_code == 200
    assert "entries" not in response.json()


@pytest.mark.parametrize("bad", ["everything", "ERRORS", "'; drop table users--"])
async def test_an_unknown_filter_is_refused(clients, bad: str) -> None:
    response = await clients["mohamed"].get(f"/api/system/log?level={bad}")

    assert response.status_code == 422


async def test_the_limit_is_bounded_by_the_ring(clients) -> None:
    """Asking for more than the ring can hold is a request that cannot be honoured."""
    assert (
        await clients["mohamed"].get(f"/api/system/log?limit={CAPACITY + 1}")
    ).status_code == 422


async def test_the_response_says_it_is_a_ring(clients) -> None:
    """Said out loud rather than left to be discovered: bounded, and empty after a
    restart. Anything needing a durable record must not read this."""
    body = (await clients["mohamed"].get("/api/system/log")).json()

    assert body["capacity"] == CAPACITY
    assert body["retained"] <= CAPACITY


def test_the_entry_shape_matches_what_the_screen_renders() -> None:
    """time · level · service · message - the columns already drawn in health.tsx."""
    entry = Entry(
        time="2026-08-27T11:04:22+00:00",
        level="warning",
        service="smtp",
        message="connect refused",
        request_id=None,
    )

    assert set(entry.as_json()) == {
        "time",
        "level",
        "service",
        "message",
        "request_id",
        "exception",
    }


def test_an_error_carries_its_traceback() -> None:
    """ "unhandled exception" with nothing under it is what a terminal is for.

    Found the hard way: a live 500 was investigated through this endpoint, and the
    endpoint had nothing to say beyond the message.
    """
    configure_logging("INFO")
    handler = recent_log_handler()
    assert handler is not None
    handler.clear()

    try:
        raise RuntimeError("no such table: backups")
    except RuntimeError:
        logging.getLogger("api.backup").exception("unhandled exception")

    entry = handler.recent()[0]
    assert entry.exception is not None
    assert "no such table: backups" in entry.exception
    assert "RuntimeError" in entry.exception


def test_a_secret_inside_a_traceback_is_redacted_too() -> None:
    """An exception's own message routinely carries the value that caused it, and the
    traceback is not the message - neither handler formatted it through the filter."""
    configure_logging("INFO")
    handler = recent_log_handler()
    assert handler is not None
    handler.clear()

    try:
        raise RuntimeError(f"rejected: token={CREDENTIAL}")
    except RuntimeError:
        logging.getLogger("api.channels").exception("could not connect")

    entry = handler.recent()[0]
    assert entry.exception is not None
    assert CREDENTIAL not in entry.exception
    assert "[redacted]" in entry.exception


def test_a_line_without_an_exception_has_none() -> None:
    """The field is null rather than an empty string: the screen renders a disclosure
    triangle only when there is something behind it."""
    handler = RecentLogHandler()
    handler.emit(logging.LogRecord("api.agent", logging.INFO, "x.py", 1, "hello", (), None))

    assert handler.recent()[0].exception is None
    assert handler.recent()[0].as_json()["exception"] is None
