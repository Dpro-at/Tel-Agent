"""Taking a backup, verifying it, and deciding which ones to keep — P7.

The retention rule is the one the backup screen states in its own copy: **fourteen
daily snapshots, then one a week for three months.** It is implemented as "keep the
newest of each day for 14 days, the newest of each ISO week for 13 weeks, and never
delete the only good one" — that last clause being the part that matters. A retention
policy that can leave an installation with zero backups is not a retention policy.

`before_update` snapshots are never pruned on age. They exist precisely because an
upgrade went wrong, and finding out that the snapshot from before the bad upgrade was
tidied away last night is a failure with no recovery.
"""

from __future__ import annotations

import datetime as dt
import logging
import traceback
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.backup import archive, targets
from api.config import Settings, get_settings
from api.models import Backup
from api.security import crypto
from api.settings import store

logger = logging.getLogger("api.backup")

# Anchored to the repository, not to the working directory. A relative path here
# resolves against wherever the process happened to be started, which is how an
# installation ends up with two backup folders and one of them empty.
ROOT = Path(__file__).resolve().parents[2]

# Settings keys, declared in the registry (api/settings/registry.py).
TARGET_KEY = "backup.target_path"
RECORDINGS_KEY = "backup.include_recordings"

DAILY_KEPT = 14
WEEKLY_KEPT = 13


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _schema_revision() -> str | None:
    """The Alembic revision this installation is at, read from the migration files.

    Read from disk rather than from `alembic_version` in the database, because the
    dump is being taken *of* that database and the two must agree by construction.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        return ScriptDirectory.from_config(config).get_current_head()
    except Exception:  # pragma: no cover - a broken alembic setup is its own alarm
        logger.warning("could not read the schema revision for the manifest")
        return None


def _key_fingerprint(settings: Settings) -> str | None:
    """The manifest's note about which key encrypted the credentials in this archive.

    None when no key is configured — a development installation with nothing
    encrypted yet. Recording None honestly is better than refusing to back up, since
    the archive is still every call and transcript on the machine.
    """
    try:
        return archive.key_fingerprint(crypto.load_key(settings.encryption_key))
    except crypto.EncryptionKeyError:
        return None


def _file_roots(*, include_recordings: bool) -> list[tuple[str, Path]]:
    """Which directories are copied in beside the database.

    Downloaded models are never included, and that is a decision the screen already
    explains: they are gigabytes, they are identical on every installation, and a
    restore re-downloads them in minutes. Including them would multiply the size of
    every nightly archive for no recoverable information at all.
    """
    if not include_recordings:
        return []
    return [("recordings", ROOT / "var" / "recordings")]


async def run_backup(
    db: DbSession, *, kind: str = "manual", settings: Settings | None = None
) -> Backup:
    """Take one backup, verify it, and record what happened.

    The row is written *before* the archive, not after. A process killed mid-write
    then leaves a `running` row, which is the only evidence the attempt happened —
    with no row, an interrupted nightly job is indistinguishable from one that never
    started, and the screen would report "backed up" from the night before.
    """
    settings = settings or get_settings()
    row = Backup(kind=kind, status="running", contents={})
    db.add(row)
    await db.commit()
    await db.refresh(row)

    try:
        configured = await store.get(db, TARGET_KEY)
        directory = targets.resolve(configured)
        if directory is None:
            if kind != "before_update":
                raise RuntimeError(
                    "no backup target is configured; choose a directory in Settings"
                )
            # An upgrade must not be blocked by an unconfigured target. Onto the local
            # disk it goes, and the manifest says so.
            directory = targets.default_directory(ROOT)

        result = targets.probe(str(directory))
        if not result.ok:
            raise RuntimeError(f"the backup target is not usable: {result.detail}")

        include_recordings = bool(await store.get(db, RECORDINGS_KEY))
        taken_at = _now()
        destination = directory / targets.usable_name(taken_at.isoformat(), kind)

        manifest = await archive.write_archive(
            db,
            destination,
            kind=kind,
            key_id=_key_fingerprint(settings),
            schema_revision=_schema_revision(),
            version=settings.version,
            file_roots=_file_roots(include_recordings=include_recordings),
            taken_at=taken_at,
        )

        row.path = str(destination)
        row.size_bytes = destination.stat().st_size
        row.checksum = archive.checksum(destination)
        row.contents = {
            "tables": manifest["tables"],
            "files": manifest["files"],
            "recordings_included": include_recordings,
            "encryption_key_fingerprint": manifest["encryption_key_fingerprint"],
        }
        row.schema_revision = manifest["schema_revision"]
        row.finished_at = _now()

        # Written. Now prove it can be read - the two are separate outcomes, and a
        # failure here does not make the file worthless, only untrusted.
        try:
            archive.verify(destination, row.checksum)
        except Exception as error:
            row.status = "unverified"
            row.error = f"written, but the read-back check failed: {error}"
            logger.error(
                "backup written but failed verification",
                extra={"backup_id": row.id, "path": str(destination)},
            )
        else:
            row.status = "ok"
            row.verified_at = _now()
            logger.info(
                "backup complete",
                extra={"backup_id": row.id, "bytes": row.size_bytes, "kind": kind},
            )
    except Exception:
        row.status = "failed"
        row.finished_at = _now()
        row.error = traceback.format_exc(limit=5)
        logger.exception("backup failed", extra={"backup_id": row.id, "kind": kind})

    await db.commit()
    await db.refresh(row)
    return row


def _keep(rows: list[Backup]) -> set[int]:
    """Which backup ids survive retention.

    The windows are ages, not counts. "Keep the fourteen most recent distinct days"
    sounds equivalent and is not: on an installation whose nightly job has been
    failing, the fourteen days present can stretch back a year, and a snapshot from
    last August survives forever while looking like a daily. Age is what the screen's
    copy promises and age is what is measured.

    Kept as a pure function over rows: retention is the one part of this that deletes
    data, so it is the part that has to be testable without a filesystem.
    """
    now = _now()
    daily_window = dt.timedelta(days=DAILY_KEPT)
    weekly_window = dt.timedelta(weeks=WEEKLY_KEPT)

    good = [r for r in rows if r.status in ("ok", "unverified")]
    keep: set[int] = set()

    # Never prune a snapshot taken before a version change. See the module docstring.
    keep.update(r.id for r in good if r.kind == "before_update")

    days: dict[dt.date, int] = {}
    weeks: dict[tuple[int, int], int] = {}
    for row in sorted(good, key=lambda r: _aware(r.started_at) or now, reverse=True):
        moment = _aware(row.started_at) or now
        age = now - moment
        if age <= daily_window:
            days.setdefault(moment.date(), row.id)
        elif age <= weekly_window:
            weeks.setdefault(moment.isocalendar()[:2], row.id)

    keep.update(days.values())
    keep.update(weeks.values())

    # The floor: whatever the rules say, an installation is never left with none.
    # A policy that can delete the last copy is not a retention policy, it is a bug
    # waiting for the week somebody's nightly job has been failing - which is exactly
    # the week every remaining copy is old enough to fall outside both windows.
    verified = [r for r in good if r.status == "ok"]
    if verified and not (keep & {r.id for r in verified}):
        newest = max(verified, key=lambda r: _aware(r.started_at) or now)
        keep.add(newest.id)

    return keep


async def prune(db: DbSession) -> int:
    """Delete the archives retention no longer keeps. Returns how many went."""
    rows = list((await db.execute(select(Backup))).scalars().all())
    keep = _keep(rows)

    removed = 0
    for row in rows:
        if row.id in keep:
            continue
        # A failed attempt has no file and nothing to learn from once a good backup
        # exists after it; its row goes with the rest so the screen is not a list of
        # old failures.
        if row.path:
            targets.remove(row.path)
        await db.delete(row)
        removed += 1

    if removed:
        await db.commit()
        logger.info("pruned old backups", extra={"removed": removed})
    return removed


async def verdict(db: DbSession) -> dict[str, Any]:
    """What the screen leads with: the one sentence about the state of this machine.

    Four states, matching the four the interface already draws — none, stale, ok, and
    a backup currently running.
    """
    rows = list(
        (await db.execute(select(Backup).order_by(Backup.started_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    running = next((r for r in rows if r.status == "running"), None)
    good = [r for r in rows if r.status == "ok"]
    latest_good = good[0] if good else None
    configured = await store.get(db, TARGET_KEY)

    if running is not None:
        state = "running"
    elif latest_good is None:
        state = "none"
    else:
        age = _now() - (_aware(latest_good.verified_at or latest_good.started_at) or _now())
        # Two days rather than one: a nightly job that has missed a single night is a
        # blip, and an alarm that cries on every blip is one an operator learns to
        # ignore - which is how the nine-day case in the screen's copy happens.
        state = "stale" if age > dt.timedelta(days=2) else "ok"

    failures_since = [
        r
        for r in rows
        if r.status == "failed" and (latest_good is None or r.id > latest_good.id)
    ]

    return {
        "state": state,
        "target_configured": bool(configured),
        "last_good_at": (_aware(latest_good.verified_at) if latest_good else None),
        "consecutive_failures": len(failures_since),
        "last_error": failures_since[0].error if failures_since else None,
    }
