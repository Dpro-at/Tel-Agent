"""The ceiling, on its own.

The endpoint's own tests prove it is wired; these prove it counts correctly, which is
the part with the edges: a refused request that must not raise the count, a window that
resets rather than decays, and two buckets that cannot spend each other's budget.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.quota import RateCounter
from api.security import quota

SMALL = quota.Limit(count=3, window=dt.timedelta(minutes=5))


async def test_it_allows_up_to_the_ceiling_and_then_stops(migrated: AsyncSession) -> None:
    for attempt in range(SMALL.count):
        assert await quota.consume(migrated, "b", SMALL) is True, attempt
    assert await quota.consume(migrated, "b", SMALL) is False


async def test_being_refused_does_not_raise_the_count(migrated: AsyncSession) -> None:
    """Otherwise hammering while refused pushes the count up and the window never ends."""
    for _ in range(SMALL.count):
        await quota.consume(migrated, "b", SMALL)
    for _ in range(20):
        assert await quota.consume(migrated, "b", SMALL) is False

    row = await migrated.scalar(select(RateCounter).where(RateCounter.bucket == "b"))
    assert row is not None
    assert row.count == SMALL.count


async def test_two_buckets_do_not_spend_each_others_budget(migrated: AsyncSession) -> None:
    for _ in range(SMALL.count):
        await quota.consume(migrated, "one", SMALL)
    assert await quota.consume(migrated, "one", SMALL) is False
    # The whole reason there are two: a script cycling threads must not be paid for out
    # of the same budget it is trying to escape.
    assert await quota.consume(migrated, "two", SMALL) is True


async def test_the_window_resets_rather_than_decays(migrated: AsyncSession) -> None:
    for _ in range(SMALL.count):
        await quota.consume(migrated, "b", SMALL)
    assert await quota.consume(migrated, "b", SMALL) is False

    row = await migrated.scalar(select(RateCounter).where(RateCounter.bucket == "b"))
    assert row is not None
    row.window_started_at = dt.datetime.now(dt.UTC) - SMALL.window - dt.timedelta(seconds=1)
    await migrated.flush()

    # A full budget, not one slot back. A decaying counter would let a steady stream sit
    # just under the ceiling forever.
    assert await quota.consume(migrated, "b", SMALL) is True
    assert row.count == 1


async def test_a_window_that_has_not_ended_keeps_counting(migrated: AsyncSession) -> None:
    await quota.consume(migrated, "b", SMALL)
    row = await migrated.scalar(select(RateCounter).where(RateCounter.bucket == "b"))
    assert row is not None
    row.window_started_at = dt.datetime.now(dt.UTC) - SMALL.window + dt.timedelta(seconds=30)
    await migrated.flush()

    assert await quota.consume(migrated, "b", SMALL) is True
    assert row.count == 2


async def test_a_very_long_bucket_key_does_not_break_the_column(
    migrated: AsyncSession,
) -> None:
    """A key is built from an origin, and an origin can be longer than the column."""
    long_key = "webchat:origin:1:https://" + "a" * 400
    assert await quota.consume(migrated, long_key, SMALL) is True
    # Truncated to the same value both times, so it is one bucket rather than a new one
    # on every request - which would be a ceiling that never applies.
    assert await quota.consume(migrated, long_key, SMALL) is True
    rows = (await migrated.execute(select(RateCounter))).scalars().all()
    assert len(rows) == 1
    assert rows[0].count == 2


async def test_stale_windows_are_removed(migrated: AsyncSession) -> None:
    await quota.consume(migrated, "old", SMALL)
    await quota.consume(migrated, "new", SMALL)

    old = await migrated.scalar(select(RateCounter).where(RateCounter.bucket == "old"))
    assert old is not None
    old.window_started_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
    await migrated.flush()

    # Nothing else ever deletes these: a bucket is abandoned by its traffic stopping,
    # which is silent, so the table grows a row per origin forever without this.
    removed = await quota.delete_stale_counters(migrated, dt.timedelta(days=1))
    assert removed == 1
    remaining = (await migrated.execute(select(RateCounter))).scalars().all()
    assert [row.bucket for row in remaining] == ["new"]


@pytest.mark.parametrize(
    ("limit", "what"),
    [
        (quota.PER_CONVERSATION, "one visitor"),
        (quota.PER_ORIGIN, "one site"),
    ],
)
def test_the_shipped_limits_are_above_anything_a_person_does(limit, what) -> None:
    """A ceiling a real customer can reach is a ceiling that refuses real customers."""
    assert limit.count >= 60, what
    assert limit.window <= dt.timedelta(minutes=15), what
