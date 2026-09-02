"""The rules engine — Milestone 4, §A6.5.

`api/routes/rules.py` has said since #89 that it is *the record, not the engine*.
This is the engine. It answers one question for one arriving contact: **pass**,
**block**, or **ai** — straight through to a person, refused, or answered by the
agent.

**A rule is written against a channel identity, not a phone number.** The roadmap's
reasoning, kept here because it shaped the signature: a WhatsApp number, a Telegram
handle and an email address all resolve to the same person, and a phone-only
matcher would have to be written a second time the day the first text channel
needed it. The caller hands in every identity it has for the sender — a chat id
and a username, say — and a rule about any of them is a rule about this contact.

**Matching order: exact before prefix, longer prefix before shorter.** The same
order the model's docstring promised the agent would enforce at Milestone 11; the
phone will reuse this function rather than reimplement it, once the caller-ID
question (§A2) is settled on a live line.

**Business hours bend `pass` and nothing else.** §A6.5: outside them the agent
always answers — there is nobody at the desk to pass to. `block` is block at any
hour: the clock changes who answers, never whether somebody unwanted gets in.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Rule

logger = logging.getLogger("api.routing")

# "mo-fr 08:00-18:00", "sa 09:00-12:00", or just "08:00-18:00" (every day).
_DAYS = ("mo", "tu", "we", "th", "fr", "sa", "su")
_HOURS = re.compile(
    r"^(?:(?P<first>mo|tu|we|th|fr|sa|su)(?:-(?P<last>mo|tu|we|th|fr|sa|su))?\s+)?"
    r"(?P<from>[0-2]\d:[0-5]\d)-(?P<to>[0-2]\d:[0-5]\d)$"
)


@dataclass(frozen=True)
class Hours:
    """A parsed business-hours setting: which weekdays, and the daily window."""

    days: frozenset[int]  # Monday is 0, like datetime.weekday()
    start: dt.time
    end: dt.time


@dataclass(frozen=True)
class Decision:
    """What an arriving contact gets, and which rule said so (None: the default)."""

    action: str
    pattern: str | None


def parse_hours(raw: str) -> Hours | None:
    """The `routing.hours` setting, or None when empty or not understood.

    None means "no business hours configured", which makes `pass` apply around the
    clock - the honest reading of an operator who never filled the field in. A
    typo'd value is treated the same and logged, never raised: routing runs on the
    message path, and a customer must not meet a stack trace because of a setting.
    """
    cleaned = (raw or "").strip().lower()
    if not cleaned:
        return None
    found = _HOURS.match(cleaned)
    if found is None:
        logger.warning("routing.hours not understood; treating as unset")
        return None

    first = found.group("first")
    last = found.group("last")
    if first is None:
        days = frozenset(range(7))
    elif last is None:
        days = frozenset({_DAYS.index(first)})
    else:
        start_day, end_day = _DAYS.index(first), _DAYS.index(last)
        span = (
            range(start_day, end_day + 1)
            if start_day <= end_day
            else [*range(start_day, 7), *range(0, end_day + 1)]
        )
        days = frozenset(span)

    def _time(value: str) -> dt.time | None:
        try:
            return dt.time.fromisoformat(value)
        except ValueError:
            return None

    start, end = _time(found.group("from")), _time(found.group("to"))
    if start is None or end is None:
        logger.warning("routing.hours not understood; treating as unset")
        return None
    return Hours(days=days, start=start, end=end)


def within(hours: Hours, local_now: dt.datetime) -> bool:
    """Whether this naive local moment falls inside the configured hours.

    An end before the start is an overnight window (18:00-02:00): inside it, the
    early-morning side belongs to the *previous* day's shift, so the day check
    follows the shift's opening day.
    """
    moment = local_now.time()
    if hours.start <= hours.end:
        return local_now.weekday() in hours.days and hours.start <= moment < hours.end
    if moment >= hours.start:
        return local_now.weekday() in hours.days
    if moment < hours.end:
        return (local_now.weekday() - 1) % 7 in hours.days
    return False


def _local(now: dt.datetime, timezone_name: str) -> dt.datetime:
    """`now` on the workspace's wall clock, or the server's when none is set."""
    cleaned = (timezone_name or "").strip()
    if cleaned:
        try:
            return now.astimezone(ZoneInfo(cleaned)).replace(tzinfo=None)
        except (KeyError, ValueError):
            logger.warning("routing.timezone not understood; using the server clock")
    return now.astimezone().replace(tzinfo=None)


async def apply_pass(db: DbSession, conversation, decision: Decision) -> None:  # noqa: ANN001
    """Hand this conversation to a person, because a rule said so.

    The transition is what notifies: a `pass` contact writing five messages is one
    handover, not five pings. Once `handling` is `human` - by this rule or by a
    colleague's own takeover - it stays human; the engine never hands a thread back
    to the agent, because it cannot know whether a person is mid-sentence in it.
    """
    from api.notifications import raise_notification

    if conversation.handling == "human":
        return
    conversation.handling = "human"
    await db.commit()
    try:
        await raise_notification(
            db,
            workspace_id=conversation.workspace_id,
            category="review",
            message_key="routed_to_person",
            params={},
            detail=decision.pattern,
            primary_action="open_conversation",
            action_payload={"conversation_id": conversation.id},
            conversation_id=conversation.id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "routed to a person but could not notify",
            extra={"conversation_id": conversation.id},
        )
    logger.info(
        "conversation routed to a person by rule",
        extra={"conversation_id": conversation.id, "pattern": decision.pattern},
    )


async def decide(
    db: DbSession,
    *,
    workspace_id: int,
    identities: Sequence[str],
    now: dt.datetime | None = None,
) -> Decision:
    """What a contact arriving under these identities gets, right now.

    Reads every rule in the workspace on each call. A workspace holds the rules a
    person typed about people they know - dozens, not thousands - and the loudest
    channels already make one database round trip per message; a cache here would
    buy microseconds and sell "why does my new rule not apply".
    """
    from api.settings import store

    wanted = [cleaned.lower() for raw in identities if (cleaned := str(raw).strip())]
    if not wanted:
        return Decision(action="ai", pattern=None)

    rows = (
        (await db.execute(select(Rule).where(Rule.workspace_id == workspace_id)))
        .scalars()
        .all()
    )

    matched: Rule | None = None
    for row in rows:
        pattern = row.pattern.lower()
        if pattern.endswith("*"):
            stem = pattern[:-1]
            if any(identity.startswith(stem) for identity in wanted):
                # Longest stem wins; an exact match (below) beats any prefix.
                if matched is None or (
                    matched.pattern.endswith("*") and len(stem) > len(matched.pattern) - 1
                ):
                    matched = row
        elif pattern in wanted:
            matched = row
            break

    if matched is None:
        return Decision(action="ai", pattern=None)

    action = matched.action
    if action == "pass":
        hours = parse_hours(
            str(await store.get(db, "routing.hours", workspace_id=workspace_id) or "")
        )
        if hours is not None:
            timezone_name = str(
                await store.get(db, "routing.timezone", workspace_id=workspace_id) or ""
            )
            moment = now if now is not None else dt.datetime.now(dt.UTC)
            if not within(hours, _local(moment, timezone_name)):
                # §A6.5: outside business hours the agent always answers.
                return Decision(action="ai", pattern=matched.pattern)

    return Decision(action=action, pattern=matched.pattern)
