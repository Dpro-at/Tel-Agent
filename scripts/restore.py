"""Restore an installation from an archive — P7.

**Run this with the application stopped.** It is a script and not an endpoint on
purpose: a process cannot safely replace the database it is holding open, and the one
failure this whole system exists to prevent is an installation left with neither the
old data nor the new. The backup screen stages a restore and asks for a restart; this
is what carries it out.

    python scripts/restore.py                     # carry out a staged restore
    python scripts/restore.py path/to/archive.tar.gz
    python scripts/restore.py --list archive.tar.gz    # look inside, change nothing

**What a restore does, said plainly:** every row in every table is deleted and
replaced with the archive's. Anything created since the backup was taken is gone, and
there is no undo — which is why the confirmation is typed and not clicked, both here
and on the screen.

**What it cannot restore.** Credentials in the archive are encrypted with the
`ENCRYPTION_KEY` that was in `.env` when the backup was taken. Restoring into an
installation with a different key gives back every call, transcript and setting, and
no working provider password. This script says so before it starts, not after.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.backup import archive  # noqa: E402
from api.config import get_settings  # noqa: E402
from api.db import create_engine, create_sessionmaker, session_scope  # noqa: E402
from api.security import crypto  # noqa: E402

RESTORE_REQUEST = ROOT / "var" / "restore-request.json"

# Rows inserted per statement. Large enough that a 200k-row table is not 200k round
# trips, small enough that one statement's parameters fit comfortably.
BATCH = 500


def _staged() -> dict | None:
    if not RESTORE_REQUEST.is_file():
        return None
    try:
        return json.loads(RESTORE_REQUEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"A restore was staged but the request file cannot be read: {error}")
        return None


def _describe(path: Path, manifest: dict) -> None:
    taken = manifest.get("taken_at", "unknown")
    print(f"\nArchive:  {path}")
    print(f"Taken:    {taken}  ({manifest.get('kind', '?')})")
    print(f"Version:  {manifest.get('version', '?')}")
    print(f"Schema:   {manifest.get('schema_revision', '?')}")
    tables = manifest.get("tables", {})
    total = sum(tables.values())
    print(f"Contents: {total} rows across {len(tables)} tables")
    for label, count in manifest.get("files", {}).items():
        print(f"          {count} {label}")
    if not manifest.get("files"):
        print("          no recordings (transcripts only)")


def _key_warning(manifest: dict) -> str | None:
    settings = get_settings()
    try:
        current = archive.key_fingerprint(crypto.load_key(settings.encryption_key))
    except crypto.EncryptionKeyError:
        current = None
    archived = manifest.get("encryption_key_fingerprint")

    if archived and current and archived != current:
        return (
            "This archive was encrypted with a DIFFERENT ENCRYPTION_KEY than this\n"
            "installation holds. Calls, transcripts and settings will restore.\n"
            "Every stored provider password will not, and cannot be recovered without\n"
            "the original key."
        )
    if archived and current is None:
        return (
            "This installation has no ENCRYPTION_KEY set, and the archive contains\n"
            "encrypted credentials. They will restore as unreadable values."
        )
    return None


def _schema_warning(manifest: dict) -> str | None:
    """Refuse a restore from a schema this installation does not know.

    An archive from a *newer* Tel-Agent carries columns this code has never heard of.
    Loading it would either fail halfway — leaving a half-emptied database — or
    silently drop them. Both are worse than refusing.
    """
    archived = manifest.get("schema_revision")
    if not archived:
        return None

    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        script = ScriptDirectory.from_config(config)
        known = {revision.revision for revision in script.walk_revisions()}
    except Exception:
        return None

    if archived not in known:
        return (
            f"This archive was taken at schema revision {archived}, which this\n"
            "installation does not have. It is from a NEWER version of Tel-Agent.\n"
            "Upgrade first, then restore."
        )
    return None


async def _load(path: Path) -> None:
    """Replace the contents of every table with the archive's.

    Deleted in reverse dependency order and inserted in forward order, inside one
    transaction. Either the whole restore lands or none of it does — a database left
    half-old and half-new is the state with no recovery path at all.
    """
    tables = archive.ordered_tables()
    settings = get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)

    async with session_scope(sessionmaker) as db:
        for table in reversed(tables):
            await db.execute(table.delete())

        for table in tables:
            batch: list[dict] = []
            inserted = 0
            for row in archive.read_table(path, table.name):
                # Columns the archive has and this schema does not are dropped, and
                # columns this schema has and the archive does not take their default.
                # That is what makes an older archive restorable into a newer schema.
                batch.append({key: value for key, value in row.items() if key in table.c})
                if len(batch) >= BATCH:
                    await db.execute(table.insert(), batch)
                    inserted += len(batch)
                    batch = []
            if batch:
                await db.execute(table.insert(), batch)
                inserted += len(batch)
            if inserted:
                print(f"  {table.name}: {inserted}")

        await db.commit()

    try:
        await _resync_sequences(sessionmaker, settings, tables)
    finally:
        await engine.dispose()


async def _resync_sequences(sessionmaker: object, settings: object, tables: list) -> None:
    """Move PostgreSQL's identity sequences past the ids that were just inserted.

    Rows are restored with their original primary keys, which does not advance the
    sequence behind the column. Without this the very next insert reuses id 1 and
    fails on the primary key - a restore that appears to work and breaks on the first
    write afterwards. SQLite derives its rowid from the table and needs nothing.
    """
    import sqlalchemy as sa
    from sqlalchemy import text

    if not settings.database_url.startswith("postgresql"):
        return

    async with session_scope(sessionmaker) as db:
        for table in tables:
            columns = list(table.primary_key.columns)
            if len(columns) != 1:
                continue
            column = columns[0]
            if not isinstance(column.type, sa.Integer):
                continue
            # Identifiers come from `Base.metadata`, never from input, so interpolating
            # them is safe; the two that could be are still bound parameters.
            # `WHERE seq IS NOT NULL` covers a primary key with no sequence behind it,
            # where `setval(NULL, ...)` would raise instead of doing nothing.
            await db.execute(
                text(
                    f"SELECT setval(s.seq, COALESCE((SELECT MAX({column.name}) "  # noqa: S608
                    f"FROM {table.name}), 1)) "
                    "FROM (SELECT pg_get_serial_sequence(:table, :column) AS seq) s "
                    "WHERE s.seq IS NOT NULL"
                ),
                {"table": table.name, "column": column.name},
            )
        await db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "archive", nargs="?", help="Archive to restore. Omit to use a staged one."
    )
    parser.add_argument("--list", action="store_true", help="Describe the archive and stop.")
    parser.add_argument(
        "--yes", action="store_true", help="Skip the typed confirmation. For automation only."
    )
    args = parser.parse_args()

    staged = None
    if args.archive:
        path = Path(args.archive).expanduser().resolve()
    else:
        staged = _staged()
        if staged is None:
            print("No restore is staged and no archive was given. Nothing to do.")
            return 0
        path = Path(staged["path"])

    if not path.is_file():
        print(f"There is no archive at {path}")
        return 1

    try:
        manifest = archive.verify(path, staged.get("checksum") if staged else None)
    except Exception as error:
        print(f"This archive cannot be read, so nothing was changed: {error}")
        return 1

    _describe(path, manifest)

    if args.list:
        return 0

    blocked = _schema_warning(manifest)
    if blocked:
        print(f"\nREFUSING TO RESTORE\n{blocked}")
        return 1

    warning = _key_warning(manifest)
    if warning:
        print(f"\nWARNING\n{warning}")

    print(
        "\nThis DELETES every call, transcript, contact and setting created since\n"
        f"{manifest.get('taken_at', 'the backup')}. There is no undo."
    )
    if not args.yes:
        typed = input("Type RESTORE to continue: ").strip()
        if typed != "RESTORE":
            print("Nothing was changed.")
            return 1

    asyncio.run(_load(path))

    if RESTORE_REQUEST.is_file():
        # Removed only after success. A restore that crashed halfway must still be
        # staged, so the operator is not left thinking it was carried out.
        RESTORE_REQUEST.unlink()

    print(f"\nRestored from {path.name} at {dt.datetime.now(dt.UTC).isoformat()}.")
    print("Start Tel-Agent again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
