"""The record of every backup that was taken — P7.

The archive lives on a disk somewhere; this table is what the screen reads. Kept
separate on purpose: a backup that exists as a file nobody catalogued is a backup
nobody knows the state of, and "there is a file in that folder" is not the same claim
as "this one was read back and restores".

`status` carries the distinction the backup screen already draws in its own copy:
a snapshot that was **written but failed its read-back check** is not a failure — the
bytes are there — and it is not a success either. It is `unverified`, and the screen
says "do not rely on this one". Collapsing those two into ok/failed would either hide
a real problem or throw away a copy that might still be the only one.

Not workspace-scoped, deliberately. A backup is of the *installation* — every
workspace on it, the settings, the schema version. There is no such thing as backing
up one tenant out of one file, and pretending otherwise with a `workspace_id` column
would invite a query that restores half a machine.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column

# `running` exists so an interrupted backup is visible rather than absent: a process
# killed mid-write leaves this row behind, which is the only evidence it was ever
# attempted.
BACKUP_STATUSES = ("running", "ok", "unverified", "failed")

# Why it was taken. `before_update` is the one nobody asks for and everybody wants:
# the screen promises "taken automatically before the version change".
BACKUP_KINDS = ("manual", "nightly", "before_update")


class Backup(Base):
    """One archive on disk, and what is known about it."""

    __tablename__ = "backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(
        enum_column(*BACKUP_KINDS, name="backup_kind"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        enum_column(*BACKUP_STATUSES, name="backup_status"), nullable=False, index=True
    )

    started_at: Mapped[dt.datetime] = utc_now_column()
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set only by a successful read-back. Null here is the whole meaning of
    # `unverified`: nothing has proved this file can be opened.
    verified_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Absolute, because the target can be changed after the file was written and a
    # path relative to "wherever the target points now" would resolve to the wrong
    # disk. Nullable while the row is `running` and the file does not exist yet.
    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # BigInteger: recordings are the bulk of an archive and 2 GB is not a ceiling.
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # SHA-256 of the file as written. Verification re-reads the file and compares, so
    # a target that silently truncated or a disk that rotted is caught here rather
    # than during a restore, which is the worst possible moment to find out.
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # What went in — the parts, and the counts per table. Enough to answer "does this
    # archive contain the recordings?" without opening a 20 GB file.
    contents: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # The migration the schema was at. A restore into a *newer* installation can run
    # migrations forward; into an older one it cannot, and refusing loudly is the only
    # safe answer.
    schema_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
