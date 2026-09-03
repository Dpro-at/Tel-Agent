"""Milestone 5's last §B7 tool — check_calendar, over CalDAV, against a fake server.

The provider is exercised on its own (the free-busy request it sends and the VFREEBUSY
it parses), and the tool is exercised through `toolset` against a stand-in CalDAV so no
real calendar is touched. What matters most: an unreachable calendar and an
unconfigured one both come back as sentences the model can act on, never a crash and
never an invented availability — the calendar rule's "one wrong entry destroys trust"
applies to a wrong *free*, too.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agent.providers.calendar import CalendarError
from agent.providers.calendar.caldav import CalDAVCalendar, _parse_freebusy
from api.agent_tools import toolset
from api.config import Settings
from api.main import create_app
from api.models import Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


# --- The VFREEBUSY parser --------------------------------------------------------

_VFREEBUSY = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VFREEBUSY\r\n"
    "FREEBUSY;FBTYPE=BUSY:20260908T090000Z/20260908T100000Z,"
    "20260908T140000Z/20260908T143000Z\r\n"
    "FREEBUSY:20260908T160000Z/PT1H\r\n"
    "END:VFREEBUSY\r\n"
    "END:VCALENDAR\r\n"
)


def test_freebusy_periods_are_parsed_including_a_duration() -> None:
    busy = _parse_freebusy(_VFREEBUSY)
    assert len(busy) == 3
    assert busy[0].start == dt.datetime(2026, 9, 8, 9, 0, tzinfo=dt.UTC)
    assert busy[0].end == dt.datetime(2026, 9, 8, 10, 0, tzinfo=dt.UTC)
    # The third is start/duration (PT1H), expanded to an end.
    assert busy[2].start == dt.datetime(2026, 9, 8, 16, 0, tzinfo=dt.UTC)
    assert busy[2].end == dt.datetime(2026, 9, 8, 17, 0, tzinfo=dt.UTC)


def test_an_empty_freebusy_is_a_free_day_not_an_error() -> None:
    empty = "BEGIN:VFREEBUSY\r\nEND:VFREEBUSY\r\n"
    assert _parse_freebusy(empty) == []


async def test_caldav_sends_a_free_busy_report_and_returns_the_periods() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content.decode()
        return httpx.Response(200, text=_VFREEBUSY)

    calendar = CalDAVCalendar(
        url="https://cal.test/dav/",
        username="mohamed",
        password="app-pw",  # noqa: S106
        transport=httpx.MockTransport(handler),
    )
    busy = await calendar.busy(
        dt.datetime(2026, 9, 8, tzinfo=dt.UTC), dt.datetime(2026, 9, 9, tzinfo=dt.UTC)
    )
    assert len(busy) == 3
    assert seen["method"] == "REPORT"
    assert "free-busy-query" in seen["body"]
    assert seen["auth"] is not None  # Basic auth was sent


async def test_caldav_raises_on_a_server_error() -> None:
    calendar = CalDAVCalendar(
        url="https://cal.test/dav/",
        username="u",
        password="p",  # noqa: S106
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with pytest.raises(CalendarError):
        await calendar.busy(
            dt.datetime(2026, 9, 8, tzinfo=dt.UTC), dt.datetime(2026, 9, 9, tzinfo=dt.UTC)
        )


# --- The tool, through toolset ---------------------------------------------------


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()
    user = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        http = AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://localhost",
        )
        assert (
            await http.post(
                "/api/auth/login", json={"username": "mohamed", "password": PASSWORD}
            )
        ).status_code == 200
        offered = toolset(app.state.sessionmaker, workspace_id=workspace.id, conversation_id=1)
        tool = next(t for t in offered if t.name == "check_calendar")
        try:
            yield http, tool
        finally:
            await http.aclose()


async def test_no_calendar_configured_is_a_sentence_not_a_failure(stage) -> None:
    _http, tool = stage
    answer = await tool.run({"date": "2026-09-08"})
    assert "No calendar is connected" in answer


async def test_a_configured_calendar_reports_busy_periods(stage, monkeypatch) -> None:
    http, tool = stage
    saved = await http.patch(
        "/api/settings",
        json={
            "values": {
                "calendar.caldav_url": "https://cal.test/dav/",
                "calendar.caldav_username": "mohamed",
                "calendar.caldav_password": "app-pw",
            }
        },
    )
    assert saved.status_code == 200, saved.text

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(
            *args,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=_VFREEBUSY)),
            **{k: v for k, v in kwargs.items() if k in ("timeout", "auth")},
        )

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    answer = await tool.run({"date": "2026-09-08"})
    assert "busy at" in answer
    assert "09:00-10:00" in answer
    # The tool must never claim it booked anything - only that it will pass it on.
    assert "pass it on to confirm" in answer


async def test_a_free_day_says_so(stage, monkeypatch) -> None:
    http, tool = stage
    await http.patch(
        "/api/settings",
        json={
            "values": {
                "calendar.caldav_url": "https://cal.test/dav/",
                "calendar.caldav_username": "u",
                "calendar.caldav_password": "p",
            }
        },
    )
    real_client = httpx.AsyncClient
    empty = "BEGIN:VFREEBUSY\r\nEND:VFREEBUSY\r\n"

    def fake_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(
            *args,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=empty)),
            **{k: v for k, v in kwargs.items() if k in ("timeout", "auth")},
        )

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    answer = await tool.run({"date": "2026-09-08"})
    assert "free all day" in answer


async def test_an_unreachable_calendar_offers_a_message_instead_of_a_time(
    stage, monkeypatch
) -> None:
    http, tool = stage
    await http.patch(
        "/api/settings",
        json={
            "values": {
                "calendar.caldav_url": "https://cal.test/dav/",
                "calendar.caldav_username": "u",
                "calendar.caldav_password": "p",
            }
        },
    )
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(
            *args,
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
            **{k: v for k, v in kwargs.items() if k in ("timeout", "auth")},
        )

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    answer = await tool.run({"date": "2026-09-08"})
    assert "could not be reached" in answer
    assert "message" in answer
