"""What this installation can actually do, and what it refused — the apps screen's
real half.

The `apps` table is written at startup by `sync_catalogue`, mirroring the manifests
the registry loaded; the registry itself knows which of those are live in *this*
process and which modules were refused. This endpoint reads both, so the screen shows
what is running rather than what was installed at some point in the past.

**Read-only, deliberately.** `Registry.disable` exists and `AppInstall.enabled` is a
real column, but nothing at runtime consults per-workspace enablement yet, and a
deactivate button whose state nothing reads would be a control that lies. The write
half arrives when the hook bus checks enablement per workspace — a design decision,
not an endpoint.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import App
from api.security.permissions import WorkspaceContext, require_admin

router = APIRouter(prefix="/api/apps", tags=["apps"])


class InstalledApp(BaseModel):
    """One extension this installation knows about, as its manifest declared it."""

    slug: str
    name: str
    version: str | None
    origin: str
    category: str
    description: str
    # The reviewable claim: what the extension asked to be allowed to do. Advisory in
    # this in-process runtime, and shown precisely because it is the honest part of
    # that trade (see api/extensions/manifest.py).
    scopes: list[str]
    hooks: list[str]
    # Live in this process right now. A row can be in the table and not running —
    # a module that loaded once and was refused on the last start.
    running: bool


class RefusedApp(BaseModel):
    """A module this process refused at start, and the reason, verbatim."""

    slug: str
    reason: str


class AppsOverview(BaseModel):
    installed: list[InstalledApp]
    refused: list[RefusedApp]


@router.get("", response_model=AppsOverview, summary="Installed applications, and refusals")
async def overview(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> AppsOverview:
    db: DbSession = request.state.db
    registry = request.app.state.extensions

    rows = (await db.execute(select(App).order_by(App.slug))).scalars().all()
    installed = [
        InstalledApp(
            slug=row.slug,
            name=str(row.manifest.get("name") or row.slug),
            version=row.version,
            origin=row.origin,
            category=str(row.manifest.get("category") or ""),
            description=str(row.manifest.get("description") or ""),
            scopes=list(row.manifest.get("scopes") or ()),
            hooks=list(row.manifest.get("hooks") or ()),
            running=row.slug in registry.loaded,
        )
        for row in rows
    ]
    refused = [RefusedApp(slug=entry.slug, reason=entry.reason) for entry in registry.failed]
    return AppsOverview(installed=installed, refused=refused)
