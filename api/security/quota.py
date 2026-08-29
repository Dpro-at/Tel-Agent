"""How many, in how long — §B14's rate limits.

**Not optional, and not conditional on reCAPTCHA.** A public endpoint that reaches a
paid model without a ceiling is a bill that arrives before anybody notices, and the
operator hears about it from the model vendor rather than from us. reCAPTCHA can be
switched off — a self-hosted product cannot make a third party mandatory — and when it
is, this is the only thing left.

**Two buckets, because they stop different things.** One conversation sending a
thousand messages is a script driving the widget; a thousand conversations from one
origin in a minute is that script restarting the thread each time to escape the first
bucket. Counting only the first would be measured by exactly the attack it invites.

**A fixed window, not a sliding one.** Its known flaw is the boundary: twice the limit
can pass across two adjacent windows. That is acceptable here and would not be for
billing - this is a ceiling on abuse, and an attacker who works out the boundary has
bought themselves one extra window's worth, not an open door. A sliding window costs a
row per request, which is the thing being limited.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models.quota import RateCounter


@dataclass(frozen=True)
class Limit:
    """A ceiling and the window it applies to."""

    count: int
    window: dt.timedelta


# What a person does versus what a script does.
#
# A visitor types perhaps ten messages in five minutes and is unusual at twenty. Sixty
# is far past any human and far below the number that costs money, which is where a
# ceiling belongs: high enough that nobody real meets it, low enough that meeting it
# repeatedly is not affordable.
PER_CONVERSATION = Limit(count=60, window=dt.timedelta(minutes=5))

# A busy shop with a chat bubble on every page. Deliberately generous - this bucket
# exists to stop a script cycling threads, not to size anybody's business, and the
# failure mode of a tight one is refusing a customer on a busy afternoon.
PER_ORIGIN = Limit(count=600, window=dt.timedelta(minutes=5))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back a naive datetime for a column that was written aware."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


async def consume(db: DbSession, bucket: str, limit: Limit) -> bool:
    """Count one against `bucket`. False when it is already at its ceiling.

    Counted before the work rather than after: a request that is refused must not also
    have cost the thing it was refused from doing.

    **Mutates; the caller commits.** A refused request has still spent nothing, but the
    window it was refused in has to survive - so a route that returns early on `False`
    commits before it does, or the refusal is forgotten and the next request starts the
    count again.
    """
    now = _now()
    row = await db.scalar(select(RateCounter).where(RateCounter.bucket == bucket[:255]))

    if row is None:
        db.add(RateCounter(bucket=bucket[:255], window_started_at=now, count=1))
        await db.flush()
        return True

    if now - _aware(row.window_started_at) >= limit.window:
        # A new window. Reset rather than decay: the point is a ceiling per window,
        # and a decaying counter would let a steady stream sit just under it forever.
        row.window_started_at = now
        row.count = 1
        return True

    if row.count >= limit.count:
        # Not incremented. Otherwise a client that keeps hammering while refused would
        # push the count up and keep the window from ever being reached honestly.
        return False

    row.count += 1
    return True


async def delete_stale_counters(db: DbSession, older_than: dt.timedelta) -> int:
    """Remove windows nothing has touched. Returns how many went.

    Without this the table grows one row per origin and per conversation for as long as
    the installation runs, and nothing else ever deletes them: a bucket is abandoned
    silently, by its traffic stopping.

    One statement rather than a row at a time: this runs over every abandoned bucket on
    an installation, and `session.delete()` per row would also leave them in the
    session's identity map until a flush - so a caller that deleted and then counted
    would be told they were still there.
    """
    cutoff = _now() - older_than
    result = await db.execute(delete(RateCounter).where(RateCounter.window_started_at < cutoff))
    return result.rowcount or 0
