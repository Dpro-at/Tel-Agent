"""The backup screen's endpoints — P7.

**Owner, not admin, for anything destructive.** Reading the state of the backups is an
`admin` job like the rest of operations. Downloading one is not: an archive is every
transcript on the installation in a single file, and a download is the shortest path
from "an admin account was phished" to "every conversation this business ever had is
on somebody else's laptop". Restoring is worse — it deletes everything since. Both are
`owner`.

**Restore does not happen here.** This endpoint stages one and asks for a restart; the
work is done by `scripts/restore.py` before the application opens the database. A
process cannot safely replace the database it is holding open, and the screen's own
copy already describes exactly this: "the phone line drops for about a minute while
the agent restarts".
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.backup import archive, service, targets
from api.backup.service import ROOT
from api.dependencies import CurrentUser
from api.models import Backup
from api.security import audit
from api.security.permissions import WorkspaceContext, require_admin, require_owner
from api.settings import store

logger = logging.getLogger("api.backup")

router = APIRouter(prefix="/api/backup", tags=["backup"])

# Read by `scripts/restore.py` on the next start. A file rather than a table row,
# because the restore replaces the table it would have to read the row from.
RESTORE_REQUEST = ROOT / "var" / "restore-request.json"


class Snapshot(BaseModel):
    id: int
    kind: str
    status: str
    started_at: dt.datetime
    verified_at: dt.datetime | None
    size_bytes: int | None
    recordings_included: bool
    schema_revision: str | None
    error: str | None
    # Whether the file is still where it was written. A USB disk that went home in
    # somebody's bag leaves the row and takes the archive, and a list that showed it
    # as restorable would be lying.
    present: bool


class TargetState(BaseModel):
    path: str
    configured: bool
    writable: bool
    detail: str
    free_bytes: int | None


class Overview(BaseModel):
    state: str
    target: TargetState
    include_recordings: bool
    last_good_at: dt.datetime | None
    consecutive_failures: int
    last_error: str | None
    snapshots: list[Snapshot]
    retention: dict[str, int]


def _snapshot(row: Backup) -> Snapshot:
    return Snapshot(
        id=row.id,
        kind=row.kind,
        status=row.status,
        started_at=row.started_at,
        verified_at=row.verified_at,
        size_bytes=row.size_bytes,
        recordings_included=bool(row.contents.get("recordings_included")),
        schema_revision=row.schema_revision,
        error=row.error,
        present=_present(row),
    )


@router.get("", response_model=Overview, summary="The state of this installation's backups")
async def overview(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> Overview:
    db: DbSession = request.state.db
    verdict = await service.verdict(db)
    configured = await store.get(db, service.TARGET_KEY)
    probe = targets.probe(configured)
    rows = (
        (await db.execute(select(Backup).order_by(Backup.started_at.desc()).limit(60)))
        .scalars()
        .all()
    )

    return Overview(
        state=verdict["state"],
        target=TargetState(
            path=probe.path,
            configured=bool(configured),
            writable=probe.ok,
            detail=probe.detail,
            free_bytes=probe.free_bytes,
        ),
        include_recordings=bool(await store.get(db, service.RECORDINGS_KEY)),
        last_good_at=verdict["last_good_at"],
        consecutive_failures=verdict["consecutive_failures"],
        last_error=verdict["last_error"],
        snapshots=[_snapshot(row) for row in rows],
        retention={"daily": service.DAILY_KEPT, "weekly": service.WEEKLY_KEPT},
    )


class Started(BaseModel):
    queued: bool
    detail: str


@router.post("/run", response_model=Started, summary="Take a backup now")
async def run_now(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> Started:
    """Enqueue it. A backup takes minutes and a request cannot hold that open.

    The target is probed here rather than only inside the job, so an operator who has
    typed a path wrong is told immediately instead of finding a failed row thirty
    seconds later.
    """
    from api.jobs.runner import enqueue

    db: DbSession = request.state.db
    configured = await store.get(db, service.TARGET_KEY)
    probe = targets.probe(configured)
    if not probe.ok:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"The backup target is not usable: {probe.detail}",
        )

    await enqueue(db, "run_backup", {"kind": "manual"})
    await db.commit()
    return Started(queued=True, detail=f"Backing up to {probe.path}")


@router.get("/target/check", response_model=TargetState, summary="Probe the backup target")
async def check_target(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> TargetState:
    """Write a file to the target and read it back.

    Separate from saving the setting on purpose. A path is accepted whether or not the
    share is up, and "the setting looks right" is exactly what an operator sees for
    the nine days in the screen's stale copy.
    """
    db: DbSession = request.state.db
    configured = await store.get(db, service.TARGET_KEY)
    probe = targets.probe(configured)
    return TargetState(
        path=probe.path,
        configured=bool(configured),
        writable=probe.ok,
        detail=probe.detail,
        free_bytes=probe.free_bytes,
    )


def _present(row: Backup) -> bool:
    """Whether the archive is still where it was written.

    One `stat`, never a directory walk. It is a blocking call on a path that may be an
    unresponsive network share, so the cost is kept to a single syscall - the honest
    alternative, moving it to a thread, buys less than it costs for one stat and would
    have to be done at every call site.
    """
    return bool(row.path) and Path(row.path).is_file()


async def _load(db: DbSession, backup_id: int) -> Backup:
    row = await db.get(Backup, backup_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such backup.")
    return row


@router.get("/{backup_id}/download", summary="Download one archive")
async def download(
    request: Request,
    context: Annotated[WorkspaceContext, require_owner],
    user: CurrentUser,
    backup_id: int,
) -> FileResponse:
    """Owner only, and audited.

    One file here is every transcript on the installation. That makes downloading it a
    data export, and a data export that leaves no trace is one nobody can investigate.
    """
    db: DbSession = request.state.db
    row = await _load(db, backup_id)
    if not _present(row):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="The archive is no longer at the path it was written to.",
        )

    await audit.record(
        db,
        "backup_downloaded",
        request=request,
        user_id=user.id,
        details={"backup_id": row.id, "bytes": row.size_bytes},
    )
    return FileResponse(row.path, media_type="application/gzip", filename=Path(row.path).name)


class RestoreRequest(BaseModel):
    # The date, typed by hand. The screen asks for it — "Type the date to confirm" —
    # and it is the only control here that stands between a mis-click and every call
    # since that date being deleted.
    confirm_date: str


class RestoreStaged(BaseModel):
    staged: bool
    detail: str
    warnings: list[str]


@router.post("/{backup_id}/restore", response_model=RestoreStaged, summary="Stage a restore")
async def stage_restore(
    request: Request,
    context: Annotated[WorkspaceContext, require_owner],
    user: CurrentUser,
    backup_id: int,
    body: RestoreRequest,
) -> RestoreStaged:
    """Validate the archive, write the request, and tell the operator to restart.

    Nothing is destroyed by this call. The archive is opened and verified first, so
    the one failure mode that must never happen — an installation wiped and then found
    to have an unreadable archive — is caught while the current data is still there.
    """
    db: DbSession = request.state.db
    row = await _load(db, backup_id)
    if not _present(row):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="The archive is no longer at the path it was written to.",
        )

    expected = row.started_at.date().isoformat()
    if body.confirm_date.strip() != expected:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Type the date of this backup to confirm: {expected}",
        )

    try:
        manifest = archive.verify(Path(row.path), row.checksum)
    except Exception as error:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"This archive cannot be read, so it will not be restored: {error}",
        ) from error

    warnings = _warnings(manifest)

    RESTORE_REQUEST.parent.mkdir(parents=True, exist_ok=True)
    RESTORE_REQUEST.write_text(
        json.dumps(
            {
                "backup_id": row.id,
                "path": row.path,
                "checksum": row.checksum,
                "requested_at": dt.datetime.now(dt.UTC).isoformat(),
                "requested_by": user.id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    await audit.record(
        db,
        "restore_staged",
        request=request,
        user_id=user.id,
        details={"backup_id": row.id, "taken_at": row.started_at.isoformat()},
    )
    logger.warning(
        "restore staged; the next start will replace the database",
        extra={"backup_id": row.id, "user_id": user.id},
    )
    return RestoreStaged(
        staged=True,
        detail="Restart Tel-Agent to carry out the restore. Nothing has changed yet.",
        warnings=warnings,
    )


def _warnings(manifest: dict[str, Any]) -> list[str]:
    """What the operator needs to hear *before* the restart, not after it."""
    from api.config import get_settings
    from api.security import crypto

    warnings: list[str] = []
    settings = get_settings()
    try:
        current = archive.key_fingerprint(crypto.load_key(settings.encryption_key))
    except crypto.EncryptionKeyError:
        current = None

    archived = manifest.get("encryption_key_fingerprint")
    if archived and current and archived != current:
        warnings.append(
            "This archive's credentials were encrypted with a different ENCRYPTION_KEY "
            "than this installation holds. Calls, transcripts and settings will restore; "
            "every stored provider password will not."
        )
    if not manifest.get("files", {}).get("recordings"):
        warnings.append("This archive contains no audio recordings, only their transcripts.")
    return warnings


@router.delete(
    "/{backup_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete one archive"
)
async def delete_backup(
    request: Request,
    context: Annotated[WorkspaceContext, require_owner],
    user: CurrentUser,
    backup_id: int,
) -> None:
    db: DbSession = request.state.db
    row = await _load(db, backup_id)
    if row.path:
        targets.remove(row.path)
    await audit.record(
        db, "backup_deleted", request=request, user_id=user.id, details={"backup_id": row.id}
    )
    await db.delete(row)
    await db.commit()
