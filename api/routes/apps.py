"""What this installation can do, what this workspace has switched on, and what the
process refused — the apps screen's real half.

The `apps` table is written at startup by `sync_catalogue`, mirroring the manifests
the registry loaded; the registry itself knows which of those are live in *this*
process and which modules were refused. `app_installs` says which of them the current
workspace has installed and switched on. The overview reads all three, so the screen
shows what is running and what is allowed to run here rather than what was installed
at some point in the past.

**The switch is per workspace, and governs both channels and the hook bus.** Switching
a channel app off switches that workspace's channels of its kind off in the same
transaction, and a channel card cannot switch its channel back on while the app is off
(see `api/extensions/installs.py`). The hook bus cache is updated in the same request
via `HookBus.set_workspace_enabled` so disabled apps receive no events for that
workspace without a database query on the message path (#242).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.extensions import installs
from api.models import App, AppInstall, Channel
from api.security import audit
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
    # This workspace's installation. Installed and switched off is a real state, and a
    # different one from not installed: it keeps the settings while the app stops.
    installed: bool
    enabled: bool
    # Part of the core: always installed, always on, and never switchable.
    system: bool


class RefusedApp(BaseModel):
    """A module this process refused at start, and the reason, verbatim."""

    slug: str
    reason: str


class AppsOverview(BaseModel):
    installed: list[InstalledApp]
    refused: list[RefusedApp]


class AppSwitch(BaseModel):
    enabled: bool


def _entry(request: Request, row: App, install: AppInstall | None) -> InstalledApp:
    system = installs.is_system(row.slug)
    return InstalledApp(
        slug=row.slug,
        name=str(row.manifest.get("name") or row.slug),
        version=row.version,
        origin=row.origin,
        category=str(row.manifest.get("category") or ""),
        description=str(row.manifest.get("description") or ""),
        scopes=list(row.manifest.get("scopes") or ()),
        hooks=list(row.manifest.get("hooks") or ()),
        running=row.slug in request.app.state.extensions.loaded,
        installed=system or install is not None,
        enabled=system or (install is not None and install.enabled),
        system=system,
    )


@router.get("", response_model=AppsOverview, summary="Installed applications, and refusals")
async def overview(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> AppsOverview:
    db: DbSession = request.state.db
    registry = request.app.state.extensions

    rows = (await db.execute(select(App).order_by(App.slug))).scalars().all()
    mine = await installs.installs_by_slug(db, context.id)
    installed = [_entry(request, row, mine.get(row.slug)) for row in rows]
    refused = [RefusedApp(slug=entry.slug, reason=entry.reason) for entry in registry.failed]
    return AppsOverview(installed=installed, refused=refused)


@router.put("/{slug}", response_model=InstalledApp, summary="Install an app, or switch it")
async def switch(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    slug: str,
    payload: AppSwitch,
) -> object:
    """Switch an app on or off in this workspace, installing it on the way if needed.

    Switching a channel app off switches this workspace's channels of that kind off in
    the same transaction. Switching one on leaves its channels as they are.
    """
    db: DbSession = request.state.db

    row = await installs.app_row(db, slug)
    if row is None:
        return envelope_response(
            status_code=status.HTTP_404_NOT_FOUND,
            code="app_not_found",
            message="This installation has no app by that name.",
        )
    if installs.is_system(slug):
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="app_is_system",
            message="This app is part of the core and cannot be switched off.",
        )

    install = await installs.install_row(db, context.id, slug)
    channels_disabled = 0
    if payload.enabled or install is not None:
        install = await installs.install(db, context.id, slug, enabled=payload.enabled)

    if not payload.enabled and row.manifest.get("category") == "channels":
        result = await db.execute(
            update(Channel)
            .where(
                Channel.workspace_id == context.id,
                Channel.kind == installs.kind_of(slug),
                # `error` too: a channel that is failing is still one trying to run.
                Channel.status != "disabled",
            )
            .values(status="disabled")
            .execution_options(synchronize_session="fetch")
        )
        channels_disabled = result.rowcount or 0

    await db.commit()
    if install is not None:
        await db.refresh(install)
    await db.refresh(row)

    # Keep the hook bus cache in step with the database so disabled apps receive no
    # events for this workspace on the next emit — without a query on the hot path.
    if not installs.is_system(slug):
        request.app.state.extensions.bus.set_workspace_enabled(
            context.id, slug, enabled=payload.enabled
        )

    await audit.record(
        db,
        "app_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={
            "workspace_id": context.id,
            "slug": slug,
            "enabled": payload.enabled,
            "channels_disabled": channels_disabled,
        },
    )
    return _entry(request, row, install)
