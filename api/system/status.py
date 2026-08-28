"""What the health screen shows, gathered honestly — the wiring phase.

`/health` stays what it is: a public liveness probe a monitor can reach without
signing in. This is the other half — the detail behind the screen — and it is behind
`admin` for the reason the log is: it names hosts, paths and providers.

**The rule this module is built around: a service that does not exist yet is not
green.** The screen draws seven rows — web, SIP, LLM, STT, TTS, database, mail — and
today only three of them have any code behind them. Reporting the other four as
healthy would be a lie that survives until the first real call; reporting them as
*down* would be a different lie, and would train the owner to ignore a red dot. So
there is a third state, `not_configured`, and it is the honest one.

Each row also carries what breaks for a caller if it stops, because a coloured dot on
its own tells an owner nothing they can act on.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.config import Settings

logger = logging.getLogger("api.system")

State = Literal["ok", "degraded", "down", "not_configured"]

# Anchored to the repository, like the backup service's own root.
ROOT = Path(__file__).resolve().parents[2]

# Past this, the database is answering but not usefully. Chosen from Rule 3's budget:
# the whole end-of-speech-to-first-audio allowance is 800 ms, so a query that alone
# takes a quarter of it is already eating the call.
SLOW_QUERY_MS = 200.0


@dataclass(frozen=True)
class Service:
    """One row of the screen."""

    id: str
    state: State
    # A measured number, in milliseconds, when there is one to measure. `None` is not
    # zero and must not be drawn as a bar of length zero.
    latency_ms: float | None = None
    # Free text from the failure itself - a refused connection names the host and port,
    # and that is the part an operator acts on.
    detail: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
        }


async def _database(db: DbSession) -> Service:
    """Reached, and how long it took to answer.

    Timed rather than merely probed: a database that answers in 900 ms is not healthy,
    and "up" would be the wrong word for it on a screen an owner reads to decide
    whether the phone can be trusted.
    """
    started = time.perf_counter()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as error:
        return Service("db", "down", detail=str(error)[:200])
    elapsed = (time.perf_counter() - started) * 1000
    return Service("db", "degraded" if elapsed > SLOW_QUERY_MS else "ok", round(elapsed, 1))


async def _mail(db: DbSession, settings: Settings) -> Service:
    """Configured at all, and if so whether the server answers.

    The forgot-password screen's `no_mail` state depends on exactly this, so it is
    checked here rather than discovered by somebody who cannot sign in.

    The settings read happens first and finishes before the probe starts. That order is
    load-bearing: the probe runs in a worker thread, and a second query issued on this
    session while it is in flight is a concurrent operation on one connection - which
    SQLAlchemy refuses, turning a health check into the outage it was watching for.
    """
    from api import mail

    config = await mail.resolve(db, settings)
    if not config.host:
        return Service("smtp", "not_configured")

    started = time.perf_counter()
    try:
        # A connect, not a send. Sending a probe message on every page load would put
        # mail in somebody's inbox every thirty seconds.
        reachable = await asyncio.to_thread(mail.can_connect, config)
    except Exception as error:
        return Service("smtp", "down", detail=str(error)[:200])
    elapsed = (time.perf_counter() - started) * 1000
    if not reachable:
        return Service("smtp", "down", detail=f"no answer from {config.host}:{config.port}")
    return Service("smtp", "ok", round(elapsed, 1))


def _storage() -> dict[str, Any]:
    """Disk, and what on this machine is using it.

    Recordings are named separately from everything else because they are the part an
    owner can decide about: they are the bulk, and excluding them from backups is a
    choice the backup screen offers. Grouping them into one "data" number would remove
    the information that makes the choice possible.
    """
    data_root = ROOT / "var"

    def _size(path: Path) -> int:
        if not path.is_dir():
            return 0
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    try:
        usage = shutil.disk_usage(ROOT)
    except OSError as error:  # pragma: no cover - a disk that cannot be measured
        logger.warning("could not read disk usage", extra={"reason": str(error)})
        return {"total_bytes": None, "free_bytes": None, "parts": {}}

    return {
        "total_bytes": usage.total,
        "free_bytes": usage.free,
        "parts": {
            "recordings": _size(data_root / "recordings"),
            "backups": _size(data_root / "backups"),
        },
    }


# The four that have no code behind them yet. Named here rather than omitted: a screen
# that simply does not draw SIP is a screen that cannot tell an owner the phone is not
# set up, which is the single most useful thing it could say before Milestone 11.
UNBUILT = ("web_channel", "sip", "llm", "stt", "tts")


async def collect(db: DbSession, settings: Settings) -> dict[str, Any]:
    """Everything the screen needs, in one round trip.

    One request rather than one per service: the screen shows them as a single verdict,
    and seven requests would let it render six healthy rows and one still spinning -
    which reads as a fault that is not there.
    """
    # Sequential, not gathered. Both of these use the one session this request holds,
    # and an `AsyncSession` is not safe to drive from two coroutines at once: the
    # second gets "this session is already executing", which would report the database
    # as down every time the mail check happened to overlap it.
    database = await _database(db)
    mail_service = await _mail(db, settings)

    services = [
        # The API answered this request, so it is up by construction. Said explicitly
        # rather than measured: a check of "can I reach myself" would be theatre.
        Service("api", "ok"),
        database,
        mail_service,
        *(Service(name, "not_configured") for name in UNBUILT),
    ]

    from api.jobs.builtin import last_task_status

    try:
        scheduler = await last_task_status(db)
    except Exception:  # pragma: no cover - an unmigrated database says so above
        scheduler = {}

    # The verdict is the worst real state, ignoring what is not configured. An
    # installation with no phone yet is not "degraded" - it is an installation with no
    # phone yet, and saying otherwise makes the word meaningless before it is needed.
    real = [s.state for s in services if s.state != "not_configured"]
    verdict = "down" if "down" in real else "degraded" if "degraded" in real else "ok"

    return {
        "verdict": verdict,
        "version": settings.version,
        "environment": settings.environment,
        "services": [service.as_json() for service in services],
        "storage": _storage(),
        "scheduler": scheduler,
    }
