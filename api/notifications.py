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

from api.models import Notification, Workspace
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
# Every parameter here is data that reads the same in any language - a path, a count,
# a task name, a date. Anything explanatory is passed as `detail` instead, which is
# shown as machine output rather than folded into a translated sentence.
MESSAGES: dict[str, frozenset[str]] = {
    # Backup - P7. The one an installation most needs to hear about, because a nightly
    # job failing is silent by nature.
    "backup_failed": frozenset(),
    "backup_unverified": frozenset(),
    "backup_no_target": frozenset(),
    "restore_staged": frozenset({"taken_at"}),
    # Scheduling - P2. The task's name is an identifier, not prose.
    "task_failed": frozenset({"task"}),
    # Mail - the failure the forgot-password screen depends on not happening quietly.
    "mail_failed": frozenset(),
    # A stranger wrote in on the web chat. Not a failure and not a decision - a thing
    # that happened, which somebody has to see. Without it the widget is a box that
    # swallows messages: they are stored, they are searchable, and nobody is told.
    #
    # Only for the first message of a thread. A visitor typing five lines is one
    # arrival, and five rows in the tray would bury the one that came from somebody
    # else - the notification is "somebody started talking to you", not "a message
    # exists".
    "web_chat_started": frozenset({"preview"}),
    # Milestone 9. A channel transport that lost its platform, and the moment it got
    # it back. The kind is an identifier the screen translates; whatever the failure
    # said travels as `detail`, shown as machine output.
    "channel_down": frozenset({"channel"}),
    "channel_recovered": frozenset({"channel"}),
    # Milestone 5, the tools that talk to the tray. What the agent wanted to say
    # travels as `detail` - it is the model's prose, machine output by definition.
    "agent_notification": frozenset(),
    "transfer_requested": frozenset(),
    # Milestone 4: a routing rule handed an arriving conversation straight to a
    # person. The rule's pattern travels as detail.
    "routed_to_person": frozenset(),
}

# Longer than this and it stops being a hint and starts being a log. The full text is
# in the log, under the request id, where an operator who needs all of it can find it.
DETAIL_LIMIT = 500


def _safe_detail(detail: str | None) -> str | None:
    """Trim the machine's words, and strip anything that looks like a credential.

    `detail` is nearly always `str(exception)`, and an exception message routinely
    carries the value that caused it. That exact shape - a SQLAlchemy parameter dump
    inside a failed INSERT - has already carried a live password into a log line in
    this codebase. A notification is the worse place for it to land: kept for thirty
    days, and readable by anybody with `viewer`.

    The same filter the log handlers use, so there is one answer to "what counts as a
    secret" rather than two that drift apart.
    """
    if not detail:
        return None
    from api.logging import redact_inline

    return redact_inline(detail)[:DETAIL_LIMIT]


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


async def _already_open(
    db: DbSession, workspace_id: int, message_key: str, params: dict[str, Any]
) -> bool:
    """Is the same situation already on the screen, unresolved?

    Compared on the message and its parameters, never on `detail`: a nightly backup
    that fails with a different error each night is still one situation, and five
    copies of it would teach the operator that this screen repeats itself — which is
    the last lesson the screen reporting failures can afford to teach.

    Params are compared in Python rather than in SQL. JSON equality differs between
    the two dialects D-029 commits to, and the open rows for one workspace are a
    handful, not a table scan.
    """
    rows = (
        (
            await db.execute(
                select(Notification).where(
                    Notification.workspace_id == workspace_id,
                    Notification.message_key == message_key,
                    Notification.resolved_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return any(row.params == params for row in rows)


async def raise_notification(
    db: DbSession,
    *,
    workspace_id: int,
    category: str,
    message_key: str,
    params: dict[str, Any] | None = None,
    detail: str | None = None,
    needs_decision: bool = False,
    primary_action: str = "none",
    action_payload: dict[str, Any] | None = None,
    conversation_id: int | None = None,
    skip_if_open: bool = False,
) -> Notification | None:
    """Record one notification and commit it. Returns None if nothing was recorded.

    Committed immediately rather than riding the caller's transaction: the thing being
    reported has usually just failed, and a rollback of that failure must not also
    erase the record that it happened.

    `skip_if_open` is for callers that run on a beat. A nightly job that fails every
    night must not add a row every night while the first one is still waiting; once
    the operator resolves it, the next failure is news again and is raised again.
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
        if skip_if_open and await _already_open(db, workspace_id, message_key, params):
            return None
        notification = Notification(
            workspace_id=workspace_id,
            category=category,
            needs_decision=needs_decision,
            message_key=message_key,
            params=params,
            detail=_safe_detail(detail),
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


async def raise_for_installation(
    db: DbSession,
    *,
    category: str,
    message_key: str,
    params: dict[str, Any] | None = None,
    detail: str | None = None,
    needs_decision: bool = False,
    primary_action: str = "none",
    action_payload: dict[str, Any] | None = None,
    skip_if_open: bool = False,
) -> list[Notification]:
    """Raise one event in every workspace, because it belongs to the machine.

    A failed nightly backup, a scheduled task that stopped running, mail that cannot
    leave the box: none of these happened *to* a workspace, but every workspace's data
    sits inside that archive and behind that mail server. The notifications table is
    scoped by workspace (D-028) and there is no installation row to hang these on —
    inventing one would put machine-level failures on a screen nobody has — so each
    workspace hears it, and each resolves its own copy.

    Returns the rows that were actually written; an empty list on a fresh installation
    with no workspace yet, or when every copy was skipped as already open.
    """
    # Checked before the loop, so a typo in the key fails even on an installation
    # that has no workspace yet — the moment nothing would otherwise catch it.
    check_message(message_key, params or {})

    try:
        workspace_ids = list((await db.execute(select(Workspace.id))).scalars().all())
    except Exception:
        logger.exception(
            "could not list workspaces to notify", extra={"message_key": message_key}
        )
        return []

    raised: list[Notification] = []
    for workspace_id in workspace_ids:
        row = await raise_notification(
            db,
            workspace_id=workspace_id,
            category=category,
            message_key=message_key,
            params=params,
            detail=detail,
            needs_decision=needs_decision,
            primary_action=primary_action,
            action_payload=action_payload,
            skip_if_open=skip_if_open,
        )
        if row is not None:
            raised.append(row)
    return raised


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
