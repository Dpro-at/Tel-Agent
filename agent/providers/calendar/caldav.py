"""CalDAV, behind the calendar interface — §B7's `check_calendar`, v1.

CalDAV covers Nextcloud, iCloud and any standards server, and it has one request built
for exactly this question: the **free-busy report** (RFC 4791 §7.10). A `REPORT` with a
time range asks the server "when is this calendar busy between X and Y", and the server
answers with a `VFREEBUSY` — busy periods it has already computed, recurrence and time
zones expanded on its side. The client parses `FREEBUSY` period lines, which are plain
`start/end` UTC pairs. No `RRULE` handling here, on purpose: that is where a hand-rolled
reader gets availability wrong, and getting it wrong is the one thing the calendar rule
forbids.

Authentication is HTTP Basic — a username and an app-specific password, which is how
Nextcloud and iCloud expose CalDAV without OAuth (Google Calendar needs OAuth and is a
separate, later provider). The credentials live encrypted in the database like every
other channel's (§B9.2); this client only receives them.

Google Calendar is deliberately not here: its API is OAuth, which is its own slice.
This is the one that ships first because it needs no consent screen to reach.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import httpx

from agent.providers.calendar.base import Busy, CalendarError

# Tight-ish: a calendar the agent checks mid-call must answer within a breath, but a
# free-busy report over a busy month is heavier than a channel ping.
_TIMEOUT = 10.0

# The free-busy report body. `{start}`/`{end}` are filled with UTC stamps; the server
# returns a VFREEBUSY covering the range.
_FREE_BUSY_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<c:free-busy-query xmlns:c="urn:ietf:params:xml:ns:caldav">'
    '<c:time-range start="{start}" end="{end}"/>'
    "</c:free-busy-query>"
)

_STAMP = "%Y%m%dT%H%M%SZ"


class CalDAVCalendar:
    """Reads busy periods over CalDAV. Satisfies `CalendarProvider` structurally.

    `url` is the calendar collection's address; `transport` is injectable so a test can
    stand in for a real server.
    """

    def __init__(
        self,
        *,
        url: str,
        username: str,
        password: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = url
        self._auth = (username, password)
        self._transport = transport

    async def busy(self, start: dt.datetime, end: dt.datetime) -> Sequence[Busy]:
        body = _FREE_BUSY_BODY.format(
            start=_to_utc(start).strftime(_STAMP), end=_to_utc(end).strftime(_STAMP)
        )
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, transport=self._transport, auth=self._auth
            ) as client:
                response = await client.request(
                    "REPORT",
                    self._url,
                    content=body,
                    headers={"Content-Type": "application/xml", "Depth": "1"},
                )
        except httpx.HTTPError as error:
            raise CalendarError(f"the calendar could not be reached: {error}") from error
        if response.status_code >= 300:
            raise CalendarError(f"the calendar server answered {response.status_code}")
        return _parse_freebusy(response.text)


def _to_utc(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.UTC) if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _parse_freebusy(document: str) -> list[Busy]:
    """The `FREEBUSY` periods out of a VFREEBUSY, as UTC intervals.

    A period is `start/end` (two UTC stamps) or `start/duration`; a `FREEBUSY` line may
    carry several, comma-separated, after an optional `;FBTYPE=...`. Lines that do not
    parse are skipped rather than raised on: a malformed period is one busy block missed
    on a source that is otherwise answering, and the safer failure there is to report
    the rest than to refuse the whole check.
    """
    periods: list[Busy] = []
    for raw in _unfold(document):
        if not raw.upper().startswith("FREEBUSY"):
            continue
        _, _, value = raw.partition(":")
        for chunk in value.split(","):
            interval = _period(chunk.strip())
            if interval is not None:
                periods.append(interval)
    return periods


def _unfold(document: str) -> list[str]:
    """iCalendar line unfolding: a line continued on the next by a leading space/tab."""
    lines: list[str] = []
    for line in document.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _period(chunk: str) -> Busy | None:
    start_text, _, end_text = chunk.partition("/")
    start = _stamp(start_text)
    if start is None or not end_text:
        return None
    if end_text[:1] == "P":
        duration = _duration(end_text)
        return Busy(start=start, end=start + duration) if duration is not None else None
    end = _stamp(end_text)
    return Busy(start=start, end=end) if end is not None else None


def _stamp(text: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(text, _STAMP).replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def _duration(text: str) -> dt.timedelta | None:
    """A small subset of ISO 8601 durations — days, hours, minutes, seconds — which is
    all a busy period uses. Anything richer (weeks, months) is refused as unparseable
    rather than guessed."""
    import re

    match = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", text
    )
    if match is None or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return dt.timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
