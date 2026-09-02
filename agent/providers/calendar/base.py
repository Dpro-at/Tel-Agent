"""What a calendar is, to the one tool that reads it — §B7's `check_calendar`.

The agent needs one thing from a calendar: **is this business busy in this window, and
when.** Not the events, not their titles — a customer asking "can I come Tuesday
morning?" is answered from busy periods, and reading out someone's appointment subjects
would leak them. So the interface returns intervals, not events.

**Read only, and that is the whole of v1.** §B7's calendar rule is explicit: the agent
*proposes and confirms*, or writes to a review calendar; it never books directly,
because one wrong entry destroys trust permanently. `check_calendar` is the read half —
the safe half — and booking is a separate, later, deliberate thing. Nothing here writes.

**Busy periods, computed by the source, not the client.** Recurring events, time zones
and all-day blocks are where a hand-rolled calendar reader gets availability subtly
wrong, and subtly wrong is worse than absent for a booking agent. So the provider asks
the source for the already-computed busy periods over a window (CalDAV's free-busy
report does exactly this) rather than fetching events and expanding recurrence itself.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Busy:
    """One interval the business is occupied. Half-open: `[start, end)`, both UTC."""

    start: dt.datetime
    end: dt.datetime


class CalendarError(RuntimeError):
    """The calendar could not be read. The tool turns this into a sentence, not a crash."""


class CalendarProvider(Protocol):
    """Reads busy periods from a business's calendar over a window."""

    async def busy(self, start: dt.datetime, end: dt.datetime) -> Sequence[Busy]:
        """The intervals the business is occupied between `start` and `end`, UTC.

        An empty sequence means free across the whole window — which is a real answer,
        not a failure. A source that cannot be reached raises `CalendarError`; the tool
        would rather tell the caller it could not check than invent an availability.
        """
        ...
