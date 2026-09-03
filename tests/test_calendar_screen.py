"""The calendar screen's endpoint — the free-busy week, honestly stated.

Four states, each a different sentence on the screen: no calendar connected, a week of
busy periods, credentials the server refused, and a server that did not answer. All
exercised against a stand-in CalDAV, so no calendar and no network is touched.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32

_VFREEBUSY = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VFREEBUSY\r\n"
    "FREEBUSY;FBTYPE=BUSY:20260908T090000Z/20260908T100000Z\r\n"
    "END:VFREEBUSY\r\n"
    "END:VCALENDAR\r\n"
)


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
        try:
            yield http
        finally:
            await http.aclose()


# Captured once, before any test patches it: a second stand-in inside one test must
# wrap the real client, not the previous stand-in.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _caldav_stands_in(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Every AsyncClient the provider opens answers from `handler` instead of a network."""
    real_client = _REAL_ASYNC_CLIENT

    def fake_client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(
            *args,
            transport=httpx.MockTransport(handler),
            **{k: v for k, v in kwargs.items() if k in ("timeout", "auth")},
        )

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)


async def _connect(http: AsyncClient) -> None:
    saved = await http.patch(
        "/api/settings",
        json={
            "values": {
                "calendar.caldav_url": "https://cal.test/dav/",
                "calendar.caldav_username": "calendar-user",
                "calendar.caldav_password": "app-pw",
                "routing.hours": "mo-fr 08:00-18:00",
                "routing.timezone": "Europe/Vienna",
            }
        },
    )
    assert saved.status_code == 200, saved.text


async def test_no_calendar_is_its_own_state_not_an_error(stage) -> None:
    answer = await stage.get("/api/calendar/availability")
    assert answer.status_code == 200
    body = answer.json()
    assert body["state"] == "unconfigured"
    assert body["busy"] == []
    assert body["source"] is None


async def test_a_connected_calendar_reports_the_weeks_busy_periods(stage, monkeypatch) -> None:
    await _connect(stage)
    _caldav_stands_in(monkeypatch, lambda request: httpx.Response(200, text=_VFREEBUSY))

    answer = await stage.get("/api/calendar/availability?start=2026-09-07")
    assert answer.status_code == 200
    body = answer.json()
    assert body["state"] == "ok"
    assert body["start"] == "2026-09-07"
    assert body["days"] == 7
    assert body["busy"] == [
        {"start": "2026-09-08T09:00:00+00:00", "end": "2026-09-08T10:00:00+00:00"}
    ]
    # The screen states the hours beside the grid without a settings permission.
    assert body["hours"] == "mo-fr 08:00-18:00"
    assert body["timezone"] == "Europe/Vienna"
    assert body["source"] == "https://cal.test/dav/"


async def test_refused_credentials_and_no_answer_are_kept_apart(stage, monkeypatch) -> None:
    await _connect(stage)

    _caldav_stands_in(monkeypatch, lambda request: httpx.Response(401))
    rejected = (await stage.get("/api/calendar/availability")).json()
    assert rejected["state"] == "rejected"

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _caldav_stands_in(monkeypatch, unreachable)
    down = (await stage.get("/api/calendar/availability")).json()
    assert down["state"] == "unreachable"


async def test_the_password_never_appears_in_the_answer(stage, monkeypatch) -> None:
    await _connect(stage)
    _caldav_stands_in(monkeypatch, lambda request: httpx.Response(200, text=_VFREEBUSY))

    answer = await stage.get("/api/calendar/availability")
    assert "app-pw" not in answer.text
    assert "calendar-user" not in answer.text  # the username stays server-side too


async def test_the_window_is_capped_at_a_week(stage) -> None:
    answer = await stage.get("/api/calendar/availability?days=30")
    assert answer.status_code == 422


# --- The settings screen's Test button --------------------------------------------


async def test_the_calendar_test_says_not_configured_before_credentials(stage) -> None:
    answer = await stage.post("/api/settings/calendar/test")
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "calendar_not_configured"


async def test_the_calendar_test_reaches_a_working_calendar(stage, monkeypatch) -> None:
    await _connect(stage)
    _caldav_stands_in(monkeypatch, lambda request: httpx.Response(200, text=_VFREEBUSY))

    answer = await stage.post("/api/settings/calendar/test")
    assert answer.status_code == 200
    body = answer.json()
    assert body["reached"] is True
    assert body["source"] == "https://cal.test/dav/"


async def test_the_calendar_test_reports_refused_credentials(stage, monkeypatch) -> None:
    await _connect(stage)
    _caldav_stands_in(monkeypatch, lambda request: httpx.Response(401))

    answer = await stage.post("/api/settings/calendar/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "calendar_refused"


async def test_the_calendar_test_reports_an_unanswering_server(stage, monkeypatch) -> None:
    await _connect(stage)

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _caldav_stands_in(monkeypatch, unreachable)
    answer = await stage.post("/api/settings/calendar/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "calendar_unreachable"
