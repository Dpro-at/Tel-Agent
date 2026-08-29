"""The counts the home screen opens with — §A6.2's first line.

Two numbers that no other endpoint can give: how much happened today, and how much of
it is waiting on a person. Everything else the screen draws it already has - the recent
threads come from `/api/conversations`, which knows how to assemble them, and
duplicating that here would be a second place to forget the workspace scope.

**"Today" is the browser's day, not the server's.** There is no timezone on a
workspace, so a business in Vienna asking at 00:30 would be told about a day that
ended ninety minutes ago if the server picked midnight UTC. The client sends the
instant its own day started and the count is taken from there, which is right for
whoever is looking without a schema change to make it right.

**`by_agent` is null when nothing is known, not zero.** `conversations.handling` is
written by the part of the product that decides who took a conversation, and until
that exists every row is null. Reporting "0 handled by the agent" would state as a
measurement something that was never measured; null lets the screen leave the clause
out entirely.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Conversation, Notification
from api.security.permissions import WorkspaceContext, require_viewer

router = APIRouter(prefix="/api/home", tags=["home"])

# How far back `since` may reach. The endpoint answers "today", and a day is at most
# 26 hours wide once every timezone offset is allowed for; the margin is for a clock
# that is a little wrong rather than for a caller who wants a year.
MAX_WINDOW = dt.timedelta(hours=36)


class HomeSummary(BaseModel):
    """What the screen says before the reader has looked at anything."""

    # Echoed back so the screen states the window it is reporting on rather than
    # assuming the one it asked for was the one used.
    since: str
    conversations: int
    # Null means "not recorded", which is not the same as none. See the module docstring.
    by_agent: int | None
    # Notifications that need a person and have not had one. The same rule the
    # notifications screen sorts by, asked as a number.
    waiting: int


@router.get("", response_model=HomeSummary, summary="Today, in two numbers")
async def home_summary(
    request: Request,
    context: Annotated[WorkspaceContext, require_viewer],
    since: Annotated[
        dt.datetime | None,
        Query(description="The instant the reader's day began. Defaults to 24 hours ago."),
    ] = None,
) -> HomeSummary:
    db: DbSession = request.state.db
    now = dt.datetime.now(dt.UTC)

    if since is None:
        start = now - dt.timedelta(hours=24)
    else:
        # A naive instant is read as UTC rather than refused: it is unambiguous about
        # the moment it means, and refusing it would make the endpoint harder to call
        # by hand for no gain in correctness.
        start = since if since.tzinfo else since.replace(tzinfo=dt.UTC)
        # Clamped rather than rejected. A clock that is slightly ahead, or a page left
        # open across midnight, is not an error the reader can do anything about - and
        # an unbounded window would make this a full-table count wearing a day's name.
        start = min(max(start, now - MAX_WINDOW), now)

    scope = (Conversation.workspace_id == context.id, Conversation.started_at >= start)

    conversations = await db.scalar(
        select(func.count()).select_from(Conversation).where(*scope)
    )
    # Two counts rather than a GROUP BY: the second is over a subset of the first, and
    # a grouped result would still need the null bucket separated out by hand.
    recorded = await db.scalar(
        select(func.count())
        .select_from(Conversation)
        .where(*scope, Conversation.handling.is_not(None))
    )
    by_agent = None
    if recorded:
        by_agent = await db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(*scope, Conversation.handling == "ai")
        )

    waiting = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.workspace_id == context.id,
            Notification.needs_decision.is_(True),
            Notification.resolved_at.is_(None),
        )
    )

    return HomeSummary(
        since=start.isoformat(),
        conversations=conversations or 0,
        by_agent=by_agent,
        # Deliberately not windowed by `since`: something that has waited since
        # Tuesday is more in need of a person than something raised this morning,
        # and a count that forgot it would say the screen was clear.
        waiting=waiting or 0,
    )
