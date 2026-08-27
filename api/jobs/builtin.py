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

logger = logging.getLogger("api.jobs")

# name -> how often. Hourly for the housekeeping: these tables grow slowly, and a
# tighter beat would buy nothing but write load.
CORE_SCHEDULE = {
    "cleanup_sessions": 3600,
    "cleanup_codes": 3600,
    "cleanup_lockouts": 3600,
    "cleanup_key_challenges": 3600,
    "health_probe": 300,
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


@job("send_email")
async def _send_email(db: DbSession, payload: dict) -> None:
    """Deliver one message off the request that asked for it.

    `smtplib` blocks, and a mail server that takes thirty seconds to answer should not
    hold a sign-in open for thirty seconds. Raising on failure is deliberate: the
    runner's backoff is what turns a mail server that is briefly down into a message
    that arrives late instead of one that is lost.
    """
    import asyncio

    settings = get_settings()
    sent = await asyncio.to_thread(
        mail.send,
        settings,
        to=payload["to"],
        subject=payload["subject"],
        body=payload["body"],
    )
    if not sent:
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
