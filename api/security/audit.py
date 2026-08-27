"""Writing audit rows, in one place so every caller records the same shape.

Recording must never break the thing it records: a full disk or a constraint slip in
the audit table must not turn a successful sign-in into a 500. So `record` swallows
its own failures — after logging them, which is the one place that is allowed to be
about the audit system itself.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from api.models import AuthEvent
from api.models.audit import EVENTS

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession as DbSession

logger = logging.getLogger("api.audit")


async def record(
    db: DbSession,
    event: str,
    *,
    request: Request | None = None,
    user_id: int | None = None,
    username: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Append one event and commit it.

    Committed immediately rather than riding the caller's transaction: an audit row
    for a *failed* attempt must survive even though the surrounding work rolled back —
    the failure is the fact being recorded.
    """
    assert event in EVENTS, f"unknown audit event {event!r} - add it to EVENTS first"  # noqa: S101

    try:
        db.add(
            AuthEvent(
                event=event,
                user_id=user_id,
                username=(username or "")[:64] or None,
                ip=request.client.host if request and request.client else None,
                user_agent=(request.headers.get("user-agent", "") if request else "")[:255]
                or None,
                details=details,
            )
        )
        await db.commit()
    except Exception:
        logger.exception("could not record audit event", extra={"event": event})
