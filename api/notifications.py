"""Raising and resolving notifications.

One entry point, `raise_notification`, so that everything which needs the operator's
attention arrives the same way and carries the same fields. Callers are the places that
already know something went wrong: a failed tool call, a blocked caller, a webhook that
gave up.

**Raising must never break what it reports on.** A notification about a failed SMS that
itself raises would lose the very fact it exists to record, so failures here are logged
and swallowed — the same rule the audit log follows, for the same reason.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Notification
from api.models.notification import ACTIONS, CATEGORIES

logger = logging.getLogger("api.notifications")


# Every message this product can raise, and the parameters each one needs.
#
# Declared here for the same reason the settings registry is declared: a key that is
# not in this table cannot be written. An unknown key is a typo, and a typo becomes a
# screen showing a raw identifier to somebody trying to find out why the phone stopped
# working. The parameter names are part of the declaration, so a caller that forgets
# one fails at the call site instead of printing `{reason}` onto the screen.
#
# **Adding a message means adding it here and to `locales/*/notifications.json` as
# `msg_<key>`.** The locale gate then refuses a build where German or Arabic is
# missing it, which is what keeps the screen from being trilingual in its furniture
# and English in its content.
MESSAGES: dict[str, frozenset[str]] = {
    # Backup - P7. The one an installation most needs to hear about, because a nightly
    # job failing is silent by nature.
    "backup_failed": frozenset({"reason"}),
    "backup_unverified": frozenset({"reason"}),
    "backup_no_target": frozenset(),
    "restore_staged": frozenset({"taken_at"}),
    # Scheduling - P2.
    "task_failed": frozenset({"task"}),
    # Mail - the failure the forgot-password screen depends on not happening quietly.
    "mail_failed": frozenset({"subject"}),
}


class UnknownMessage(ValueError):
    """A key that is not in the catalogue, or parameters that do not match it."""


def check_message(message_key: str, params: dict[str, Any]) -> None:
    """Refuse a message the screen could not render.

    Checked at the moment of raising rather than at the moment of display: a
    notification is read hours later by somebody with a problem, and "the message is
    broken" is the worst thing they could find waiting for them.
    """
    required = MESSAGES.get(message_key)
    if required is None:
        raise UnknownMessage(
            f"unknown notification message {message_key!r}. Add it to MESSAGES and to "
            f"locales/*/notifications.json as 'msg_{message_key}'."
        )
    missing = required - set(params)
    if missing:
        raise UnknownMessage(
            f"notification {message_key!r} needs {sorted(missing)}, which the caller "
            "did not pass"
        )


async def raise_notification(
    db: DbSession,
    *,
    workspace_id: int,
    category: str,
    message_key: str,
    params: dict[str, Any] | None = None,
    needs_decision: bool = False,
    primary_action: str = "none",
    action_payload: dict[str, Any] | None = None,
    conversation_id: int | None = None,
) -> Notification | None:
    """Record one notification and commit it. Returns None if recording failed.

    Committed immediately rather than riding the caller's transaction: the thing being
    reported has usually just failed, and a rollback of that failure must not also
    erase the record that it happened.
    """
    assert category in CATEGORIES, f"unknown category {category!r}"  # noqa: S101
    assert primary_action in ACTIONS, f"unknown action {primary_action!r}"  # noqa: S101

    # Raised, not swallowed. Everything below this line is written defensively because
    # a notification must not break what it reports on - but an unknown message key is
    # a programming error in the *caller*, and hiding it would leave the screen with a
    # row nobody can read and no clue where it came from.
    params = params or {}
    check_message(message_key, params)

    try:
        notification = Notification(
            workspace_id=workspace_id,
            category=category,
            needs_decision=needs_decision,
            message_key=message_key,
            params=params,
            primary_action=primary_action,
            action_payload=action_payload,
            conversation_id=conversation_id,
        )
        db.add(notification)
        await db.commit()
    except Exception:
        logger.exception(
            "could not record notification",
            extra={"category": category, "workspace_id": workspace_id},
        )
        return None

    logger.info(
        "notification raised",
        extra={
            "category": category,
            "message_key": message_key,
            "needs_decision": needs_decision,
            "workspace_id": workspace_id,
        },
    )
    return notification


async def resolve(
    db: DbSession, notification: Notification, *, user_id: int | None = None
) -> None:
    """Mark one item dealt with. The caller commits.

    Idempotent: resolving something already resolved keeps the original timestamp, so
    two people clicking the same button do not rewrite when it was actually handled.
    """
    if notification.resolved_at is None:
        notification.resolved_at = dt.datetime.now(dt.UTC)
        notification.resolved_by = user_id


async def open_count(db: DbSession, workspace_id: int) -> int:
    """How many things are waiting on a decision — the home screen's badge.

    Decisions only. The log below them is not a count anybody needs to see on the
    home screen, and including it would make the badge a number nobody can act on.
    """
    return (
        await db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.workspace_id == workspace_id,
                Notification.needs_decision.is_(True),
                Notification.resolved_at.is_(None),
            )
        )
        or 0
    )


async def mark_log_read(db: DbSession, workspace_id: int, *, user_id: int) -> int:
    """ "Mark all as read", exactly as the screen means it.

    Resolves the informational log and leaves anything waiting on a decision open. A
    version that cleared everything would file away the failed SMS without anybody
    ever deciding what to do about it, which is the one thing this screen is for.
    """
    rows = (
        (
            await db.execute(
                select(Notification).where(
                    Notification.workspace_id == workspace_id,
                    Notification.needs_decision.is_(False),
                    Notification.resolved_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    now = dt.datetime.now(dt.UTC)
    for row in rows:
        row.resolved_at = now
        row.resolved_by = user_id
    await db.commit()
    return len(rows)
