"""What the calendar screen can honestly show — the free-busy week.

The screen used to be a drawing: named appointments, an agent-bookings rail, a booking
dialog. None of that data exists. What does exist is §B7's CalDAV free-busy source
(#149): busy periods with no names, because RFC 4791's free-busy report deliberately
carries none — which is also the privacy-correct thing to put in front of a `viewer`.

So this endpoint answers exactly what the server can keep: whether a calendar is
connected, the week's busy periods, and the business-hours setting the same screen
links to. Booking is not here on purpose — the calendar rule is propose-and-confirm,
never write.

The fetch state is part of the answer rather than an HTTP failure: a calendar that
refuses its credentials and one that cannot be reached need different words on the
screen, and both still have a screen to draw (`configured`, the source, the hours).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from agent.providers.calendar import CalDAVCalendar, CalendarError
from api.security.permissions import WorkspaceContext, require_viewer
from api.settings import store

logger = logging.getLogger("api.calendar")

router = APIRouter(prefix="/api/calendar", tags=["calendar"])

# A week is what the screen draws; anything wider is a report, not a glance.
MAX_DAYS = 7


class BusyPeriod(BaseModel):
    start: str  # UTC ISO instants; the browser renders them on its own clock
    end: str


class Availability(BaseModel):
    """One week of what the connected calendar will say about itself."""

    # `unconfigured` is its own state, not an error: nothing is wrong, nothing is
    # connected. `rejected` (the server refused the credentials) and `unreachable`
    # (no answer at all) need different advice, so they are kept apart.
    state: Literal["ok", "unconfigured", "rejected", "unreachable"]
    # The CalDAV collection address - configuration, not a secret; the username and
    # password never leave the server.
    source: str | None
    start: str  # echoed back, the first day actually reported on
    days: int
    busy: list[BusyPeriod]
    # The raw `routing.hours` / `routing.timezone` settings, so the screen beside the
    # grid states them without a second request or a settings-read permission.
    hours: str | None
    timezone: str | None


@router.get("/availability", response_model=Availability, summary="The free-busy week")
async def availability(
    request: Request,
    context: Annotated[WorkspaceContext, require_viewer],
    start: Annotated[
        dt.date | None,
        Query(description="First day to report on. Defaults to today (UTC)."),
    ] = None,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = MAX_DAYS,
) -> Availability:
    db: DbSession = request.state.db

    url = str(await store.get(db, "calendar.caldav_url", workspace_id=context.id) or "").strip()
    username = str(
        await store.get(db, "calendar.caldav_username", workspace_id=context.id) or ""
    )
    password = str(
        await store.get(db, "calendar.caldav_password", workspace_id=context.id) or ""
    )
    hours = str(await store.get(db, "routing.hours", workspace_id=context.id) or "") or None
    timezone = (
        str(await store.get(db, "routing.timezone", workspace_id=context.id) or "") or None
    )

    first = start if start is not None else dt.datetime.now(dt.UTC).date()
    begin = dt.datetime(first.year, first.month, first.day, tzinfo=dt.UTC)

    def answer(
        state: Literal["ok", "unconfigured", "rejected", "unreachable"],
        busy: list[BusyPeriod],
    ) -> Availability:
        return Availability(
            state=state,
            source=url or None,
            start=first.isoformat(),
            days=days,
            busy=busy,
            hours=hours,
            timezone=timezone,
        )

    if not url:
        return answer("unconfigured", [])

    calendar = CalDAVCalendar(url=url, username=username, password=password)
    try:
        periods = await calendar.busy(begin, begin + dt.timedelta(days=days))
    except CalendarError as error:
        logger.warning("calendar availability failed: %s", error)
        rejected = error.status in (401, 403)
        return answer("rejected" if rejected else "unreachable", [])

    return answer(
        "ok",
        [
            BusyPeriod(start=period.start.isoformat(), end=period.end.isoformat())
            for period in periods
        ],
    )
