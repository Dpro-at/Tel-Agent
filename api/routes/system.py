"""The health screen's own endpoints — P6.

`/health` stays where it is and stays public: a monitor cannot sign in, and an
unreachable health check is a dead monitor. What lands here is the part of the screen
that shows *why* something is red — the recent log — and that is a different thing
with a different audience.

**The log needs `admin`, not `viewer`.** Every other read in this product is scoped to
a workspace; a log line is not. It carries hostnames, provider names, file paths, the
shape of the internals, and the mistakes of whoever is operating the machine. On an
installation a whole small business shares, the receptionist who may read every
transcript has no reason to read the SMTP handshake — and giving it to them is how an
internal detail leaves the building in a screenshot.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.logging import recent_log_handler
from api.security.permissions import WorkspaceContext, require_admin
from api.syslog import CAPACITY

router = APIRouter(prefix="/api/system", tags=["system"])


class LogEntry(BaseModel):
    time: str
    level: str
    service: str
    message: str
    request_id: str | None


class LogPage(BaseModel):
    entries: list[LogEntry]
    # Said out loud rather than left for somebody to discover: this is a ring in
    # memory, it holds at most `capacity` lines, and a restart empties it.
    capacity: int
    retained: int


@router.get(
    "/log",
    summary="Recent log lines, for the health screen",
    response_model=LogPage,
)
async def recent_log(
    context: Annotated[WorkspaceContext, require_admin],
    level: Annotated[str, Query(pattern="^(all|errors|warnings|calls)$")] = "all",
    limit: Annotated[int, Query(ge=1, le=CAPACITY)] = 100,
) -> LogPage:
    """The four filters the screen draws: all, errors, warnings, calls.

    `warnings` includes errors, because the chip means "at least this serious" — what
    somebody clicking it wants. A version showing warnings alone would hide the errors
    underneath them, which is the opposite of the question being asked.
    """
    handler = recent_log_handler()
    if handler is None:
        # Logging was configured by something other than `configure_logging` - a test
        # harness, or an embedding host. Empty is the honest answer; inventing lines
        # would be worse than showing none.
        return LogPage(entries=[], capacity=CAPACITY, retained=0)

    entries = handler.recent(level=level, limit=limit)
    return LogPage(
        entries=[LogEntry(**entry.as_json()) for entry in entries],
        capacity=CAPACITY,
        retained=len(handler.entries),
    )
