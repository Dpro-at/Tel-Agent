"""The tasks and jobs the core ships with.

Four of these existed as functions nothing called. `delete_expired_sessions`,
`delete_expired_codes`, `delete_stale_counters` and `delete_expired_challenges` were
each written alongside the feature that needed them and then left orphaned — every one
of those tables grew forever. Registering them here is the whole point of P2.
"""

from __future__ import annotations

import logging

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api import mail
from api.config import get_settings
from api.jobs.runner import job, task
from api.models import ScheduledTask
from api.security.codes import delete_expired_codes
from api.security.lockout import delete_stale_counters
from api.security.session import delete_expired_sessions
from api.security.ssh_keys import delete_expired_challenges
from api.settings import store

logger = logging.getLogger("api.jobs")

# name -> how often. Hourly for the housekeeping: these tables grow slowly, and a
# tighter beat would buy nothing but write load.
CORE_SCHEDULE = {
    "cleanup_sessions": 3600,
    "cleanup_codes": 3600,
    "cleanup_lockouts": 3600,
    "cleanup_key_challenges": 3600,
    "health_probe": 300,
    # Nightly. The screen says "03:00, when nobody is calling"; the runner schedules on
    # an interval rather than at a wall-clock time, so this is a daily beat whose first
    # run is set when the schedule row is created. A cron-style time is worth having
    # and is not worth blocking the backup on.
    "backup_nightly": 86400,
}


@task("cleanup_sessions")
async def _cleanup_sessions(db: DbSession) -> None:
    removed = await delete_expired_sessions(db)
    if removed:
        logger.info("cleaned expired sessions", extra={"removed": removed})


@task("cleanup_codes")
async def _cleanup_codes(db: DbSession) -> None:
    removed = await delete_expired_codes(db)
    if removed:
        logger.info("cleaned expired codes", extra={"removed": removed})


@task("cleanup_lockouts")
async def _cleanup_lockouts(db: DbSession) -> None:
    removed = await delete_stale_counters(db)
    if removed:
        logger.info("cleaned stale lockout counters", extra={"removed": removed})


@task("cleanup_key_challenges")
async def _cleanup_key_challenges(db: DbSession) -> None:
    removed = await delete_expired_challenges(db)
    if removed:
        logger.info("cleaned expired key challenges", extra={"removed": removed})


@task("health_probe")
async def _health_probe(db: DbSession) -> None:
    """Check the dependencies on a beat, not only when somebody asks.

    §B8's point, and the reason the roadmap calls health "not a feature": a silently
    dead service is worse than an obviously dead one, because the operator finds out
    after losing ten conversations. `/health` answers whoever asks; this asks on their
    behalf, so the failure is in the log with a timestamp before anyone notices.

    The probe records its own verdict through the task row's `last_status`, which is
    what the health endpoint reads back to say when each dependency was last seen
    alive — no second table for something the scheduler already stores.
    """
    # The probe rides the session it was handed rather than opening a connection of
    # its own. Asking the engine for a second connection while this session holds a
    # transaction on the same SQLite file blocks on the file lock and then reports
    # "unreachable" - a probe that manufactures the failure it is watching for.
    await db.execute(text("SELECT 1"))


@task("backup_nightly")
async def _backup_nightly(db: DbSession) -> None:
    """Take the nightly backup, then prune what retention no longer keeps.

    Pruning runs *after* a successful backup and never before one. Deleting old
    archives first would, on the night the target is unreachable, leave an
    installation with fewer copies than it started with — which is the opposite of
    what a backup system is for.
    """
    from api import notifications
    from api.backup import service as backup_service

    if not await store.get(db, backup_service.TARGET_KEY):
        # No target chosen. Not a failure: the screen already says loudly that there
        # is no backup of this installation, and a task that failed nightly would bury
        # that message under an error nobody can act on from here. It is still the
        # situation `backup_no_target` exists for, so it is said once — one open item
        # per workspace, not a fresh one every night — and said again only after
        # somebody resolves it without actually choosing a target.
        await notifications.raise_for_installation(
            db,
            category="review",
            message_key="backup_no_target",
            needs_decision=True,
            skip_if_open=True,
        )
        return

    row = await backup_service.run_backup(db, kind="nightly")
    if row.status == "failed":
        # Raised before the re-raise, so the operator hears about it even though the
        # task record fails. A nightly job failing is silent by nature — the catalogue
        # says this is the one an installation most needs to hear about.
        await notifications.raise_for_installation(
            db,
            category="failure",
            message_key="backup_failed",
            detail=row.error,
            needs_decision=True,
            skip_if_open=True,
        )
        raise RuntimeError(row.error or "backup failed")
    if row.status == "unverified":
        # The bytes are there and could not be read back. Not a failed task — the
        # archive may well be fine — but not a copy anybody should trust either, and
        # trusting it silently is how "known to restore" stops being true.
        await notifications.raise_for_installation(
            db,
            category="review",
            message_key="backup_unverified",
            detail=row.error,
            needs_decision=True,
            skip_if_open=True,
        )
    await backup_service.prune(db)


@job("run_backup")
async def _run_backup(db: DbSession, payload: dict) -> None:
    """The "Back up now" button, off the request that pressed it.

    A backup of a database with recordings takes minutes; a request cannot hold that
    open. So the button enqueues and the screen watches the row appear.
    """
    from api.backup import service as backup_service

    row = await backup_service.run_backup(db, kind=payload.get("kind", "manual"))
    if row.status == "failed":
        raise RuntimeError(row.error or "backup failed")


@job("send_email")
async def _send_email(db: DbSession, payload: dict) -> None:
    """Deliver one message off the request that asked for it.

    `smtplib` blocks, and a mail server that takes thirty seconds to answer should not
    hold a sign-in open for thirty seconds. Raising on failure is deliberate: the
    runner's backoff is what turns a mail server that is briefly down into a message
    that arrives late instead of one that is lost.
    """
    import asyncio

    from api import notifications

    settings = get_settings()
    # Resolved at send time, not at enqueue time: an operator who fixes the mail
    # server in Settings should rescue the messages already queued, not only the
    # next ones.
    config = await mail.resolve(db, settings)
    sent = await asyncio.to_thread(
        mail.send,
        config,
        to=payload["to"],
        subject=payload["subject"],
        body=payload["body"],
    )
    if not sent:
        # Told on the first failed attempt, not after the last: the person this most
        # often fails is somebody locked out and waiting for a reset code, and six
        # hours of quiet backoff is exactly the "quietly" the forgot-password screen
        # depends on this not being. Deduplicated while unresolved, so the retries do
        # not add a line each. The address is not in it — a notification is read by
        # anybody with `viewer`, and who was sent a reset code is personal data.
        await notifications.raise_for_installation(
            db,
            category="failure",
            message_key="mail_failed",
            skip_if_open=True,
        )
        raise RuntimeError("mail delivery failed")


async def last_task_status(db: DbSession) -> dict[str, dict[str, object]]:
    """What the scheduler last saw, for the health endpoint."""
    rows = (await db.execute(select(ScheduledTask))).scalars().all()
    return {
        row.name: {
            "enabled": row.enabled,
            "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
            "last_status": row.last_status,
            "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        }
        for row in rows
    }
