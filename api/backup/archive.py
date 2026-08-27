"""What a Tel-Agent archive is, and how one is written and read — P7.

**A logical dump, not a physical one.** Every table is walked through SQLAlchemy and
written as JSON Lines; the archive is a gzipped tar of those files plus a manifest.
The obvious alternative is `pg_dump` / a copy of the SQLite file, and it was rejected
for three reasons:

* It needs a binary on the machine, at a version matching the server. The installation
  this product is built for is one machine in an office, and "backups stopped working
  because the package manager upgraded Postgres" is a failure the operator finds out
  about during a restore.
* D-029 requires both dialects to be first class. A physical dump is one per dialect,
  which means a restore path per dialect and a test matrix that only ever runs half.
* A logical dump restores *across* dialects. An installation that outgrows SQLite
  moves to PostgreSQL by restoring a backup, which is the migration path a self-hoster
  would otherwise have to be talked through by hand.

The cost, stated rather than hidden: it is slower than `pg_dump` and it is not
byte-exact. For a database whose size is measured in hundreds of megabytes — the
recordings are the bulk, and they are copied as files — that is the right trade.

**The archive does not contain the encryption key, and this is not an oversight.**
Credential columns are stored encrypted with `ENCRYPTION_KEY` from `.env` (§B9.2) and
they travel into the archive still encrypted. An archive on a network share is
therefore not a set of usable provider passwords. The consequence is the one thing an
operator must be told before they need it: **a restore without the original
`ENCRYPTION_KEY` gives back every call and transcript, and no working credential.**
The manifest records a fingerprint of the key so a restore can say so up front rather
than after the fact.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import logging
import tarfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.db import Base

logger = logging.getLogger("api.backup")

# Bumped when the layout changes in a way an older reader cannot handle. A reader that
# meets a higher number refuses rather than guessing.
FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
TABLE_DIR = "database"
FILE_DIR = "files"

# Rows held in memory at once while dumping one table. The whole point of streaming
# is that a 4 GB messages table does not become 4 GB of Python objects.
CHUNK = 500


def key_fingerprint(key: bytes) -> str:
    """A stable, non-reversible id for the encryption key.

    Recorded so a restore can tell the operator "this archive's credentials were
    encrypted with a different key than this installation holds" *before* the restore,
    instead of leaving them to discover it when the phone provider rejects the login.
    Hashed, so the manifest never carries anything derived usefully from the key.
    """
    return hashlib.sha256(b"telagent-key-fingerprint:" + key).hexdigest()[:16]


def _encode(value: Any) -> Any:
    """JSON cannot hold what a database column can."""
    if isinstance(value, dt.datetime):
        # Normalised to UTC and marked as such. A naive timestamp restored into a
        # different machine's timezone silently moves every call by hours.
        aware = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
        return {"__type__": "datetime", "value": aware.astimezone(dt.UTC).isoformat()}
    if isinstance(value, dt.date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, bytes):
        return {"__type__": "bytes", "value": value.hex()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and "__type__" in value:
        kind = value["__type__"]
        if kind == "datetime":
            return dt.datetime.fromisoformat(value["value"])
        if kind == "date":
            return dt.date.fromisoformat(value["value"])
        if kind == "bytes":
            return bytes.fromhex(value["value"])
    return value


def ordered_tables() -> list[Any]:
    """Tables in an order that satisfies the foreign keys, parents first.

    SQLAlchemy sorts this from the metadata rather than from a hand-written list,
    which is what stops a table added by a later migration from being silently left
    out of every backup taken afterwards.
    """
    return list(Base.metadata.sorted_tables)


async def _dump_table(db: DbSession, table: Any) -> tuple[bytes, int]:
    """One table as JSON Lines. Returns the bytes and the row count."""
    buffer = io.BytesIO()
    rows = 0
    result = await db.stream(select(table))
    async for partition in result.partitions(CHUNK):
        for row in partition:
            record = {key: _encode(value) for key, value in row._mapping.items()}
            buffer.write(json.dumps(record, ensure_ascii=False).encode("utf-8"))
            buffer.write(b"\n")
            rows += 1
    return buffer.getvalue(), rows


def _add_bytes(tar: tarfile.TarFile, name: str, payload: bytes, when: float) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mtime = int(when)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(payload))


async def write_archive(
    db: DbSession,
    destination: Path,
    *,
    kind: str,
    key_id: str | None,
    schema_revision: str | None,
    version: str,
    file_roots: Iterable[tuple[str, Path]] = (),
    taken_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Write one archive and return its manifest.

    `file_roots` are (label, directory) pairs copied in verbatim — recordings and
    anything else that lives outside the database. Absent directories are skipped
    rather than being an error: an installation that has never recorded a call has no
    recordings directory, and that is not a fault.

    Written to a temporary name and renamed at the end. A backup interrupted halfway
    must not leave a file that looks complete sitting in the folder somebody will
    reach for during an outage.
    """
    taken_at = taken_at or dt.datetime.now(dt.UTC)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")

    counts: dict[str, int] = {}
    files: dict[str, int] = {}

    with partial.open("wb") as raw:
        # `mtime=0` on the gzip member, not on the entries: it keeps the container
        # header from varying, so an unchanged archive hashes the same twice.
        with (
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
            tarfile.open(fileobj=gz, mode="w") as tar,
        ):
            for table in ordered_tables():
                payload, rows = await _dump_table(db, table)
                counts[table.name] = rows
                _add_bytes(
                    tar, f"{TABLE_DIR}/{table.name}.jsonl", payload, taken_at.timestamp()
                )

            for label, root in file_roots:
                if not root.is_dir():
                    continue
                total = 0
                for path in sorted(root.rglob("*")):
                    if not path.is_file():
                        continue
                    tar.add(
                        path, arcname=f"{FILE_DIR}/{label}/{path.relative_to(root).as_posix()}"
                    )
                    total += 1
                files[label] = total

            manifest = {
                "format_version": FORMAT_VERSION,
                "product": "tel-agent",
                "version": version,
                "kind": kind,
                "taken_at": taken_at.astimezone(dt.UTC).isoformat(),
                "schema_revision": schema_revision,
                # Not the key. See the module docstring - this is what lets a restore
                # warn before it runs, instead of after.
                "encryption_key_fingerprint": key_id,
                "tables": counts,
                "files": files,
            }
            _add_bytes(
                tar,
                MANIFEST_NAME,
                json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
                taken_at.timestamp(),
            )

    partial.replace(destination)
    return manifest


def checksum(path: Path) -> str:
    """SHA-256, read in chunks so the hash of a 20 GB archive is not 20 GB of memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict[str, Any]:
    """Open the archive and read its manifest, raising if it is not one of ours."""
    try:
        with tarfile.open(path, mode="r:gz") as tar:
            member = tar.extractfile(MANIFEST_NAME)
            if member is None:
                raise ValueError("archive has no manifest")
            manifest = json.loads(member.read().decode("utf-8"))
    except (KeyError, tarfile.TarError, OSError) as error:
        # A tar with no manifest raises KeyError, and a file that is not a tar at all
        # raises TarError. Both mean the same thing to whoever pointed a restore at
        # the wrong file, and both must arrive as that sentence rather than as a
        # traceback from inside the standard library.
        raise ValueError(f"this file is not a readable Tel-Agent archive: {error}") from error

    if manifest.get("product") != "tel-agent":
        raise ValueError("not a Tel-Agent archive")
    if manifest.get("format_version", 0) > FORMAT_VERSION:
        raise ValueError(
            f"archive format {manifest['format_version']} is newer than this "
            f"installation understands ({FORMAT_VERSION}); upgrade before restoring"
        )
    return manifest


def verify(path: Path, expected_checksum: str | None = None) -> dict[str, Any]:
    """Read the archive back and prove it can be opened.

    This is the check the backup screen promises in its own words — "read back and
    verified after writing, so it is known to restore". It is deliberately more than a
    checksum: every member is decompressed, so a truncated tar or a corrupted gzip
    member is found here rather than during a restore.

    What it does not prove is that the *data* restores cleanly into a schema that has
    moved on. Nothing short of an actual restore proves that, and a nightly job that
    restored into a scratch database would be a much larger promise than this makes.
    """
    if expected_checksum is not None:
        actual = checksum(path)
        if actual != expected_checksum:
            raise ValueError(
                f"checksum mismatch: the file on disk is not the file that was written "
                f"(expected {expected_checksum[:12]}…, found {actual[:12]}…)"
            )

    manifest = read_manifest(path)
    seen: set[str] = set()
    with tarfile.open(path, mode="r:gz") as tar:
        for member in tar:
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            # Read it, do not keep it. Decompressing is the point; the bytes are not.
            while handle.read(1024 * 1024):
                pass
            seen.add(member.name)

    missing = [
        f"{TABLE_DIR}/{name}.jsonl"
        for name in manifest.get("tables", {})
        if f"{TABLE_DIR}/{name}.jsonl" not in seen
    ]
    if missing:
        raise ValueError(
            f"archive is missing {len(missing)} table(s): {', '.join(missing[:3])}"
        )

    return manifest


def read_table(path: Path, table_name: str) -> Iterable[dict[str, Any]]:
    """Rows of one table out of an archive, decoded back into Python values."""
    with tarfile.open(path, mode="r:gz") as tar:
        try:
            handle = tar.extractfile(f"{TABLE_DIR}/{table_name}.jsonl")
        except KeyError:
            return
        if handle is None:
            return
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line.decode("utf-8"))
            yield {key: _decode(value) for key, value in record.items()}
