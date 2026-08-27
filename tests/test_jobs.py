"""Scheduling and background jobs — P2.

The runner is driven directly rather than by waiting on the loop: a test that sleeps
for a tick is a test that is slow when it passes and flaky when it does not. The loop
itself is one `while True` around the two functions exercised here.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.db import create_engine, create_sessionmaker
from api.jobs import runner
from api.jobs.builtin import CORE_SCHEDULE
from api.models import BackgroundJob, ScheduledTask


@pytest.fixture
async def sessionmaker(
    migrated: AsyncSession, settings: Settings, database_url: str
) -> async_sessionmaker:
    """A sessionmaker onto the same migrated database the fixture prepared."""
    engine = create_engine(settings.model_copy(update={"database_url": database_url}))
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean_registry():
    """Handlers registered by a test do not leak into the next one."""
    tasks = dict(runner._tasks)
    jobs = dict(runner._jobs)
    yield
    runner._tasks.clear()
    runner._tasks.update(tasks)
    runner._jobs.clear()
    runner._jobs.update(jobs)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# --- Scheduled tasks ---------------------------------------------------------


async def test_a_due_task_runs_and_reschedules_itself(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    ran: list[int] = []

    @runner.task("probe")
    async def probe(db) -> None:
        ran.append(1)

    migrated.add(ScheduledTask(name="probe", interval_seconds=60, next_run_at=_now()))
    await migrated.commit()

    assert await runner.run_due_tasks(sessionmaker) == 1
    assert ran == [1]

    migrated.expire_all()
    row = await migrated.scalar(select(ScheduledTask).where(ScheduledTask.name == "probe"))
    assert row is not None
    assert row.last_status == "ok"
    # Moved forward, so the next tick does not run it again.
    assert row.next_run_at.replace(tzinfo=dt.UTC) > _now()

    assert await runner.run_due_tasks(sessionmaker) == 0
    assert ran == [1]


async def test_a_task_that_is_not_due_does_not_run(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    @runner.task("later")
    async def later(db) -> None:
        raise AssertionError("must not run")

    migrated.add(
        ScheduledTask(
            name="later", interval_seconds=60, next_run_at=_now() + dt.timedelta(hours=1)
        )
    )
    await migrated.commit()

    assert await runner.run_due_tasks(sessionmaker) == 0


async def test_a_disabled_task_does_not_run(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    """An operator who switched a task off has made a decision."""

    @runner.task("off")
    async def off(db) -> None:
        raise AssertionError("must not run")

    migrated.add(
        ScheduledTask(name="off", interval_seconds=60, next_run_at=_now(), enabled=False)
    )
    await migrated.commit()

    assert await runner.run_due_tasks(sessionmaker) == 0


async def test_a_failing_task_records_the_error_and_still_reschedules(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    """A task that throws must not stop its own schedule - or it fails once, silently,
    and never runs again."""

    @runner.task("broken")
    async def broken(db) -> None:
        raise RuntimeError("the disk is full")

    migrated.add(ScheduledTask(name="broken", interval_seconds=60, next_run_at=_now()))
    await migrated.commit()

    await runner.run_due_tasks(sessionmaker)

    migrated.expire_all()
    row = await migrated.scalar(select(ScheduledTask).where(ScheduledTask.name == "broken"))
    assert row is not None
    assert row.last_status == "failed"
    assert "disk is full" in (row.last_error or "")
    assert row.next_run_at.replace(tzinfo=dt.UTC) > _now()


async def test_a_task_with_no_handler_keeps_its_schedule(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    """Usually a disabled extension. Deleting the row would lose the operator's
    interval and enabled flag the moment the extension came back."""
    migrated.add(
        ScheduledTask(name="from_an_extension", interval_seconds=60, next_run_at=_now())
    )
    await migrated.commit()

    await runner.run_due_tasks(sessionmaker)

    migrated.expire_all()
    assert await migrated.scalar(
        select(ScheduledTask).where(ScheduledTask.name == "from_an_extension")
    )


async def test_ensure_schedule_does_not_overrule_an_operator(migrated: AsyncSession) -> None:
    await runner.ensure_schedule(migrated, "housekeeping", 3600)

    row = await migrated.scalar(
        select(ScheduledTask).where(ScheduledTask.name == "housekeeping")
    )
    assert row is not None
    row.interval_seconds = 86400
    row.enabled = False
    await migrated.commit()

    # A restart calls this again; the operator's choices survive it.
    await runner.ensure_schedule(migrated, "housekeeping", 3600)

    migrated.expire_all()
    row = await migrated.scalar(
        select(ScheduledTask).where(ScheduledTask.name == "housekeeping")
    )
    assert row is not None
    assert row.interval_seconds == 86400
    assert row.enabled is False


# --- Background jobs ---------------------------------------------------------


async def test_a_queued_job_runs_once_and_is_marked_done(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    seen: list[dict] = []

    @runner.job("greet")
    async def greet(db, payload) -> None:
        seen.append(payload)

    await runner.enqueue(migrated, "greet", {"name": "Wagner"})
    await migrated.commit()

    assert await runner.run_due_jobs(sessionmaker) == 1
    assert seen == [{"name": "Wagner"}]

    migrated.expire_all()
    row = await migrated.scalar(select(BackgroundJob))
    assert row is not None
    assert row.status == "done"
    assert row.attempts == 1
    assert row.finished_at is not None

    # And not again on the next tick.
    assert await runner.run_due_jobs(sessionmaker) == 0
    assert len(seen) == 1


async def test_enqueueing_an_unknown_kind_fails_at_the_call_site(
    migrated: AsyncSession,
) -> None:
    """Better than a row nothing can ever run, discovered by an operator later."""
    with pytest.raises(ValueError):
        await runner.enqueue(migrated, "nobody_handles_this")


async def test_a_failing_job_retries_with_backoff(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    attempts: list[int] = []

    @runner.job("flaky")
    async def flaky(db, payload) -> None:
        attempts.append(1)
        raise RuntimeError("the mail server is down")

    await runner.enqueue(migrated, "flaky")
    await migrated.commit()

    await runner.run_due_jobs(sessionmaker)

    migrated.expire_all()
    row = await migrated.scalar(select(BackgroundJob))
    assert row is not None
    assert row.status == "queued"  # back in the queue, not lost
    assert row.attempts == 1
    assert "mail server is down" in (row.last_error or "")
    # Not immediately: the next attempt is a backoff away.
    assert row.next_attempt_at.replace(tzinfo=dt.UTC) > _now()

    # A tick right now finds nothing due.
    assert await runner.run_due_jobs(sessionmaker) == 0
    assert len(attempts) == 1


async def test_a_job_gives_up_after_max_attempts(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    """Retrying forever is how a poisoned job becomes a permanent load."""

    @runner.job("doomed")
    async def doomed(db, payload) -> None:
        raise RuntimeError("nope")

    await runner.enqueue(migrated, "doomed")
    await migrated.commit()
    row = await migrated.scalar(select(BackgroundJob))
    assert row is not None
    row.max_attempts = 2
    await migrated.commit()

    for _ in range(2):
        # Make it due again, standing in for the backoff having elapsed.
        migrated.expire_all()
        row = await migrated.scalar(select(BackgroundJob))
        row.next_attempt_at = _now() - dt.timedelta(seconds=1)
        await migrated.commit()
        await runner.run_due_jobs(sessionmaker)

    migrated.expire_all()
    row = await migrated.scalar(select(BackgroundJob))
    assert row is not None
    assert row.status == "failed"
    assert row.attempts == 2
    assert row.finished_at is not None


async def test_a_claimed_job_cannot_be_claimed_twice(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    """The conditional UPDATE is the lock. Two ticks overlapping - a reload in
    development, two workers later - must not run one job twice."""

    @runner.job("once")
    async def once(db, payload) -> None:
        pass

    await runner.enqueue(migrated, "once")
    await migrated.commit()

    async with sessionmaker() as first, sessionmaker() as second:
        claimed = await runner._claim_one(first)
        assert claimed is not None
        # The second tick finds nothing left to take.
        assert await runner._claim_one(second) is None


async def test_a_job_with_no_handler_fails_rather_than_looping(
    migrated: AsyncSession, sessionmaker: async_sessionmaker
) -> None:
    """The handler was removed after the job was queued - an extension disabled
    between enqueue and run. Retrying cannot help."""
    migrated.add(BackgroundJob(kind="vanished", payload={}, next_attempt_at=_now()))
    await migrated.commit()

    await runner.run_due_jobs(sessionmaker)

    migrated.expire_all()
    row = await migrated.scalar(select(BackgroundJob))
    assert row is not None
    assert row.status == "failed"
    assert "no handler" in (row.last_error or "")


# --- The core schedule -------------------------------------------------------


def test_every_core_schedule_entry_has_a_handler() -> None:
    """A scheduled name with no code behind it is housekeeping that never happens -
    which is precisely the state these four cleanups were in before P2."""
    import api.jobs.builtin  # noqa: F401 - registers the handlers

    missing = set(CORE_SCHEDULE) - set(runner.registered_tasks())

    assert not missing, f"scheduled with no handler: {sorted(missing)}"


def test_the_orphaned_cleanups_are_all_scheduled() -> None:
    """The four functions that existed with no caller. Naming them here means the
    next one written cannot quietly join them."""
    assert {
        "cleanup_sessions",
        "cleanup_codes",
        "cleanup_lockouts",
        "cleanup_key_challenges",
    } <= set(CORE_SCHEDULE)
