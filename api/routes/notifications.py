"""The notifications screen's endpoints.

Every route is scoped to the acting workspace through `CurrentWorkspace`, so a
notification about one business can never surface in another's list — D-028's rule
applied at the one place that reads this table.

**Reading needs `viewer`; resolving needs `reception`.** A read-only account exists to
watch without changing anything, and resolving an item is a change: it is the record
that a decision was taken, by a named person, at a time. The role matrix on the
settings screen says the same thing in its own words — "Reads calls. Changes nothing,
answers nothing."
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api import notifications
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Notification
from api.security.permissions import CurrentWorkspace, WorkspaceContext, require_reception

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: int
    category: str
    needs_decision: bool
    # A key and its parameters. The screen holds the sentences, in three languages;
    # the server holds which sentence and what goes in it.
    message_key: str
    params: dict[str, Any]
    primary_action: str
    action_payload: dict[str, Any] | None
    conversation_id: int | None
    created_at: str
    resolved_at: str | None


class NotificationList(BaseModel):
    """The screen's two sections, already separated.

    Split here rather than in the browser because the distinction is the product's,
    not the layout's: one list is work, the other is history.
    """

    waiting: list[NotificationOut]
    log: list[NotificationOut]
    open_count: int


def _utc(value: dt.datetime | None) -> str | None:
    """Serialise a timestamp as UTC, always with an offset.

    SQLite hands back naive datetimes and PostgreSQL hands back aware ones, so the same
    row would serialise as `...797468` on one dialect and `...797468+00:00` on the
    other - and, worse, differently within one dialect depending on whether the value
    came from the session's identity map or from a fresh read. A client parsing a naive
    string applies its own timezone, which is how a notification resolved at 13:44 UTC
    shows up an hour out.
    """
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=dt.UTC)).isoformat()


def _out(row: Notification) -> NotificationOut:
    return NotificationOut(
        id=row.id,
        category=row.category,
        needs_decision=row.needs_decision,
        message_key=row.message_key,
        params=row.params or {},
        primary_action=row.primary_action,
        action_payload=row.action_payload,
        conversation_id=row.conversation_id,
        created_at=_utc(row.created_at),
        resolved_at=_utc(row.resolved_at),
    )


@router.get(
    "", summary="Everything waiting, and the log below it", response_model=NotificationList
)
async def list_notifications(
    request: Request,
    context: CurrentWorkspace,
    category: str | None = None,
    limit: int = 50,
) -> NotificationList:
    db: DbSession = request.state.db

    query = select(Notification).where(Notification.workspace_id == context.id)
    if category:
        query = query.where(Notification.category == category)

    rows = (
        (await db.execute(query.order_by(Notification.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )

    return NotificationList(
        waiting=[_out(r) for r in rows if r.needs_decision and r.resolved_at is None],
        log=[_out(r) for r in rows if not (r.needs_decision and r.resolved_at is None)],
        open_count=await notifications.open_count(db, context.id),
    )


@router.post(
    "/{notification_id}/resolve",
    summary="Record that this was dealt with",
    response_model=NotificationOut,
)
async def resolve_notification(
    request: Request,
    notification_id: int,
    user: CurrentUser,
    context: Annotated[WorkspaceContext, require_reception],
) -> object:
    db: DbSession = request.state.db

    row = await db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            # The workspace filter is part of the lookup, not a check afterwards: an
            # id belonging to another workspace must be indistinguishable from one
            # that does not exist.
            Notification.workspace_id == context.id,
        )
    )
    if row is None:
        return envelope_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="not_found",
            message="No such notification.",
        )

    await notifications.resolve(db, row, user_id=user.id)
    await db.commit()
    return _out(row)


class MarkedRead(BaseModel):
    resolved: int
    still_waiting: int


@router.post(
    "/mark-log-read",
    summary="Clear the log, leave the decisions",
    response_model=MarkedRead,
)
async def mark_log_read(
    request: Request,
    user: CurrentUser,
    context: Annotated[WorkspaceContext, require_reception],
) -> MarkedRead:
    """ "Mark all as read" - and the name is the screen's, not a description of the effect.

    Anything waiting on a decision stays put. Clearing those would file away a promised
    SMS that never went out without anybody deciding what to do about it.
    """
    db: DbSession = request.state.db
    resolved = await notifications.mark_log_read(db, context.id, user_id=user.id)
    return MarkedRead(
        resolved=resolved, still_waiting=await notifications.open_count(db, context.id)
    )
