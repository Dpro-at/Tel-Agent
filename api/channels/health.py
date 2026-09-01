"""What each channel transport last reported — Milestone 9's memory.

The roadmap's sentence: *a silently dead service is worse than an obviously dead one,
because the operator only finds out after losing ten conversations.* The transports
already know their own state — a poll that failed, a gateway that dropped, a webhook
that arrived signed — and this module is where they say so, so the health screen can
show it and the tray can shout when it changes.

**In-process on purpose.** The transports run inside the API process (the lifespan
starts them), so the freshest truth about a connection lives here, not in a table. A
row written per poll would be a write every second per channel for data that is
stale the moment the process restarts anyway. The cost is honesty about scope: after
a restart the registry is empty until each transport reports again — seconds for the
pollers, one supervisor beat for the sockets, the next probe or delivery for the
webhook channels — and the screen says "nothing heard yet" for exactly that long.

**The alert is the transition, not the state.** A channel that is down raises one
tray notification when it goes down (`skip_if_open` keeps the nightly retry from
raising thirty), and one quiet system note when it comes back. A red row on a screen
nobody has open is not an alert; the tray is where this product tells people things.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Channel

logger = logging.getLogger("api.channel_health")

# What a credential that cannot be decrypted means, said the way `api/llm.py` says it
# for the settings store - one wording for one fault, wherever it is reported.
UNREADABLE_CREDENTIAL = (
    "The stored credential cannot be decrypted. ENCRYPTION_KEY has changed since it "
    "was saved - restore the old one, or save the credential again."
)


async def usable_channels(db: DbSession, kinds: tuple[str, ...]) -> list[Channel]:
    """The active channels of these kinds whose credentials actually decrypt.

    Loading a `Channel` row decrypts its credential column on the way in, so one row
    saved under a key this installation no longer has would raise out of a plain
    SELECT - and take every other channel of the transport down with it, silently.
    That is the exact failure this milestone exists to end: the ids are read first
    (nothing encrypted in that query), each entity is loaded on its own, and a row
    that cannot be read is reported down with the §B9.2 sentence and skipped - the
    rest of the fleet keeps answering.
    """
    ids = (
        (
            await db.execute(
                select(Channel.id).where(Channel.kind.in_(kinds), Channel.status == "active")
            )
        )
        .scalars()
        .all()
    )
    rows: list[Channel] = []
    for channel_id in ids:
        try:
            row = await db.get(Channel, channel_id)
        except Exception:
            plain = (
                await db.execute(
                    select(Channel.id, Channel.workspace_id, Channel.kind).where(
                        Channel.id == channel_id
                    )
                )
            ).one_or_none()
            if plain is None:
                continue
            reference = SimpleNamespace(
                id=plain.id, workspace_id=plain.workspace_id, kind=plain.kind
            )
            await report_down(db, reference, detail=UNREADABLE_CREDENTIAL)
            continue
        if row is not None:
            rows.append(row)
    return rows


@dataclass
class Report:
    """One channel's last word about itself."""

    state: str  # "ok" | "down"
    detail: str | None
    at: dt.datetime
    # The last reply's whole journey, generation to delivery, when one has happened.
    # Rule 4: measured from the first message, not from the first complaint.
    last_reply_ms: float | None = None


# (kind, channel_id) -> Report. One process, one registry - see the module docstring.
_REPORTS: dict[tuple[str, int], Report] = {}


def reset() -> None:
    """Tests start from silence; nothing else calls this."""
    _REPORTS.clear()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def snapshot() -> dict[tuple[str, int], Report]:
    return dict(_REPORTS)


def note_reply(kind: str, channel_id: int, elapsed_ms: float) -> None:
    """Rule 4's number, per channel: how long the last answer took, whole.

    Logged as well as kept, so the trail survives the process even though the
    registry does not.
    """
    existing = _REPORTS.get((kind, channel_id))
    if existing is not None:
        existing.last_reply_ms = round(elapsed_ms, 1)
    logger.info(
        "reply delivered",
        extra={"channel": kind, "channel_id": channel_id, "ms": round(elapsed_ms, 1)},
    )


async def report_ok(db: DbSession, channel: Channel, *, detail: str | None = None) -> None:
    """The transport heard from its platform. Announces a recovery when it is one."""
    key = (channel.kind, channel.id)
    previous = _REPORTS.get(key)
    kept_latency = previous.last_reply_ms if previous else None
    _REPORTS[key] = Report(state="ok", detail=detail, at=_now(), last_reply_ms=kept_latency)

    if previous is not None and previous.state == "down":
        logger.info(
            "channel recovered", extra={"channel": channel.kind, "channel_id": channel.id}
        )
        from api.notifications import raise_notification

        await raise_notification(
            db,
            workspace_id=channel.workspace_id,
            category="system",
            message_key="channel_recovered",
            params={"channel": channel.kind},
        )


async def report_down(db: DbSession, channel: Channel, *, detail: str) -> None:
    """The transport could not reach its platform. Announces the transition once.

    `skip_if_open` is what makes a retry loop one alert instead of one per attempt:
    the open notification *is* the record that the operator has not dealt with it.
    """
    key = (channel.kind, channel.id)
    previous = _REPORTS.get(key)
    kept_latency = previous.last_reply_ms if previous else None
    _REPORTS[key] = Report(
        state="down", detail=detail[:200], at=_now(), last_reply_ms=kept_latency
    )

    if previous is None or previous.state != "down":
        logger.warning(
            "channel down",
            extra={"channel": channel.kind, "channel_id": channel.id, "detail": detail[:200]},
        )
        from api.notifications import raise_notification

        await raise_notification(
            db,
            workspace_id=channel.workspace_id,
            category="failure",
            message_key="channel_down",
            params={"channel": channel.kind},
            detail=detail,
            needs_decision=True,
            skip_if_open=True,
        )
