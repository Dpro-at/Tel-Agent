"""The scheduler and the job worker — P2.

Four cleanup functions were written across this codebase and **nothing ever called
them**: expired sessions, expired codes, stale lockout counters, spent key challenges.
Every one of those tables grows forever on a live installation. That gap is what this
module closes, and it is why scheduling counts as foundational rather than a nicety.

**One process, two loops, started from the application lifespan.** No separate worker
to deploy, because the smallest installation is one machine and a person who does not
run a process manager. When the installation grows, the same loops move behind a flag
without the calling code noticing.

**Claiming is a conditional UPDATE, not a SELECT then an UPDATE.** Two processes — a
reload in development, two workers later — must never run one job twice, and the
database is the only thing that can settle that race. `UPDATE ... WHERE status =
'queued'` and a rowcount of one is the claim.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import traceback
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models import BackgroundJob, ScheduledTask

logger = logging.getLogger("api.jobs")

# A handler takes a session and, for jobs, the payload. Returning normally is success;
# raising is failure, and the runner decides what that costs.
TaskHandler = Callable[[DbSession], Awaitable[None]]
JobHandler = Callable[[DbSession, dict], Awaitable[None]]

_tasks: dict[str, TaskHandler] = {}
_jobs: dict[str, JobHandler] = {}

# How often the loops wake. Not the schedule itself - a task due every twelve hours is
# still checked on this beat and simply is not due.
TICK_SECONDS = 30.0

# Backoff between attempts: 1m, 5m, 15m, 1h, 6h. A webhook endpoint that is down for
# an hour should not be hammered every thirty seconds, and a transient failure should
# not wait six hours for its first retry.
BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def task(name: str) -> Callable[[TaskHandler], TaskHandler]:
    """Register a recurring task handler by name."""

    def register(handler: TaskHandler) -> TaskHandler:
        _tasks[name] = handler
        return handler

    return register


def job(kind: str) -> Callable[[JobHandler], JobHandler]:
    """Register a background job handler by kind."""

    def register(handler: JobHandler) -> JobHandler:
        _jobs[kind] = handler
        return handler

    return register


def registered_tasks() -> dict[str, TaskHandler]:
    return dict(_tasks)


async def enqueue(
    db: DbSession, kind: str, payload: dict | None = None, *, delay_seconds: int = 0
) -> BackgroundJob:
    """Queue one job. The caller commits — enqueueing joins the caller's transaction.

    Deliberately transactional: a job that says "send the confirmation email" must not
    exist if the thing being confirmed was rolled back.
    """
    if kind not in _jobs:
        raise ValueError(f"no handler registered for job kind {kind!r}")
    row = BackgroundJob(
        kind=kind,
        payload=payload or {},
        next_attempt_at=_now() + dt.timedelta(seconds=delay_seconds),
    )
    db.add(row)
    return row


async def ensure_schedule(
    db: DbSession, name: str, interval_seconds: int, *, first_run_delay: int = 60
) -> None:
    """Create the row for a task if it is missing, leaving an existing one alone.

    Left alone on purpose: an operator who disabled a task or widened its interval has
    made a decision, and a restart must not quietly overrule it.
    """
    existing = await db.scalar(select(ScheduledTask).where(ScheduledTask.name == name))
    if existing is not None:
        return
    db.add(
        ScheduledTask(
            name=name,
            interval_seconds=interval_seconds,
            next_run_at=_now() + dt.timedelta(seconds=first_run_delay),
        )
    )
    await db.commit()


async def run_due_tasks(sessionmaker: async_sessionmaker) -> int:
    """Run every task whose time has come. Returns how many ran."""
    ran = 0
    async with sessionmaker() as db:
        due = (
            (
                await db.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.enabled.is_(True), ScheduledTask.next_run_at <= _now()
                    )
                )
            )
            .scalars()
            .all()
        )

        for row in due:
            handler = _tasks.get(row.name)
            if handler is None:
                # Usually a disabled extension. The schedule outlives it.
                logger.warning("no handler for scheduled task", extra={"task": row.name})
                row.next_run_at = _now() + dt.timedelta(seconds=row.interval_seconds)
                await db.commit()
                continue

            # Claim by moving the next run forward *before* doing the work: a task that
            # crashes the process must not be retried in a tight loop on restart.
            row.next_run_at = _now() + dt.timedelta(seconds=row.interval_seconds)
            row.last_run_at = _now()
            await db.commit()

            try:
                await handler(db)
                row.last_status, row.last_error = "ok", None
                ran += 1
            except Exception as error:
                row.last_status = "failed"
                row.last_error = traceback.format_exc(limit=5)
                logger.exception("scheduled task failed", extra={"task": row.name})
                # The operator's copy of the line above. A scheduled task fails with
                # nobody watching — that is what "scheduled" means — and the log is
                # read after somebody already knows something is wrong. Deduplicated
                # while unresolved, so an hourly task that keeps failing is one line
                # on the screen, not a wall of them.
                from api import notifications

                await notifications.raise_for_installation(
                    db,
                    category="system",
                    message_key="task_failed",
                    params={"task": row.name},
                    detail=str(error) or repr(error),
                    skip_if_open=True,
                )
            await db.commit()
    return ran


async def run_due_jobs(sessionmaker: async_sessionmaker, limit: int = 20) -> int:
    """Attempt up to `limit` queued jobs. Returns how many were attempted."""
    attempted = 0
    async with sessionmaker() as db:
        for _ in range(limit):
            claimed = await _claim_one(db)
            if claimed is None:
                break
            attempted += 1
            await _attempt(db, claimed)
    return attempted


async def _claim_one(db: DbSession) -> BackgroundJob | None:
    """Take one due job, atomically.

    The conditional UPDATE is the lock: whichever process changes the row from
    `queued` to `running` owns it, and the loser's rowcount is zero.
    """
    candidate = await db.scalar(
        select(BackgroundJob)
        .where(BackgroundJob.status == "queued", BackgroundJob.next_attempt_at <= _now())
        .order_by(BackgroundJob.next_attempt_at)
        .limit(1)
    )
    if candidate is None:
        return None

    result = await db.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == candidate.id, BackgroundJob.status == "queued")
        .values(status="running", attempts=BackgroundJob.attempts + 1)
    )
    await db.commit()
    if (result.rowcount or 0) == 0:
        return None  # somebody else claimed it first

    await db.refresh(candidate)
    return candidate


async def _attempt(db: DbSession, row: BackgroundJob) -> None:
    handler = _jobs.get(row.kind)
    if handler is None:
        row.status = "failed"
        row.last_error = f"no handler registered for kind {row.kind!r}"
        row.finished_at = _now()
        await db.commit()
        return

    try:
        await handler(db, row.payload)
    except Exception:
        row.last_error = traceback.format_exc(limit=5)
        if row.attempts >= row.max_attempts:
            row.status = "failed"
            row.finished_at = _now()
            logger.exception("job failed permanently", extra={"kind": row.kind})
        else:
            backoff = BACKOFF_SECONDS[min(row.attempts - 1, len(BACKOFF_SECONDS) - 1)]
            row.status = "queued"
            row.next_attempt_at = _now() + dt.timedelta(seconds=backoff)
            logger.warning(
                "job failed; will retry",
                extra={"kind": row.kind, "attempt": row.attempts, "in_seconds": backoff},
            )
    else:
        row.status = "done"
        row.finished_at = _now()
    await db.commit()


async def loop(sessionmaker: async_sessionmaker, *, interval: float = TICK_SECONDS) -> None:
    """The single background loop, started and cancelled by the app lifespan.

    Every iteration is wrapped: a failure inside one tick must not kill the loop, or
    scheduling stops silently and the tables start growing again — the exact failure
    this module exists to end.
    """
    while True:
        try:
            await run_due_tasks(sessionmaker)
            await run_due_jobs(sessionmaker)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("job loop iteration failed")
        await asyncio.sleep(interval)
