"""Which apps a workspace has installed, and whether each one is switched on — D-031.

`apps` is what this installation knows about; `app_installs` is what one workspace has
turned on. This module is the one place that reads and writes the second table, so
the rule "off under Apps means off" is written once rather than in every channel card.

**A channel app and its channel are two switches, and the outer one wins.** Switching
an app off switches that workspace's channels of its kind off with it (see
`api/routes/apps.py`); switching a channel on asks `admit_channel` first, so a card
cannot turn back on what the workspace turned off one level up. Switching an app on
does not switch any channel on: the channel still needs its credentials, and a
channel that starts answering customers because somebody flipped a different switch
is a surprise nobody asked for.

**The system apps are not optional.** `agent_core` and `database` are the core
registering itself through the same contract (D-031); there is no workspace without
them, so they read as installed and enabled and cannot be switched.
"""

from __future__ import annotations

from fastapi import status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.errors import envelope_response
from api.models import App, AppInstall

# Always on, never switchable.
SYSTEM_APPS: tuple[str, ...] = ("agent_core", "database")

# What every new workspace starts with. Web chat is the first channel (D-017), and a
# workspace with no channel app at all has no way for anybody to reach it.
DEFAULT_APPS: tuple[str, ...] = ("web_chat",)

# The one channel whose app slug and channel kind differ: the kind was `web` from the
# first migration, and the app was named `web_chat` by its manifest. Every other
# channel app is named after its kind.
_KIND_BY_SLUG: dict[str, str] = {"web_chat": "web"}
_SLUG_BY_KIND: dict[str, str] = {kind: slug for slug, kind in _KIND_BY_SLUG.items()}


def kind_of(slug: str) -> str:
    """The `channels.kind` a channel app serves."""
    return _KIND_BY_SLUG.get(slug, slug)


def slug_of(kind: str) -> str:
    """The app that serves a channel kind."""
    return _SLUG_BY_KIND.get(kind, kind)


def is_system(slug: str) -> bool:
    return slug in SYSTEM_APPS


async def app_row(db: DbSession, slug: str) -> App | None:
    return await db.scalar(select(App).where(App.slug == slug))


async def install_row(db: DbSession, workspace_id: int, slug: str) -> AppInstall | None:
    return await db.scalar(
        select(AppInstall)
        .join(App, App.id == AppInstall.app_id)
        .where(AppInstall.workspace_id == workspace_id, App.slug == slug)
    )


async def installs_by_slug(db: DbSession, workspace_id: int) -> dict[str, AppInstall]:
    """Every installation in one workspace, keyed by app slug."""
    rows = await db.execute(
        select(App.slug, AppInstall)
        .join(App, App.id == AppInstall.app_id)
        .where(AppInstall.workspace_id == workspace_id)
    )
    return {slug: row for slug, row in rows.tuples()}


async def is_enabled(db: DbSession, workspace_id: int, slug: str) -> bool:
    """Installed and switched on in this workspace. System apps always are."""
    if is_system(slug):
        return True
    row = await install_row(db, workspace_id, slug)
    return row is not None and row.enabled


async def install(
    db: DbSession, workspace_id: int, slug: str, *, enabled: bool = True
) -> AppInstall:
    """Install an app in a workspace, or set the switch on the installation it has.

    Does not commit: the caller decides what else belongs to the same transaction.

    The `apps` row is normally written at startup by `sync_catalogue`. A workspace can
    be created before that has ever run - the first-run setup of a fresh installation
    whose catalogue sync failed, or a test that never started the application - so a
    missing row is added here with an empty manifest, and the next sync fills it in.
    """
    app = await app_row(db, slug)
    if app is None:
        app = App(slug=slug, origin="official", manifest={})
        db.add(app)
        await db.flush()

    row = await db.scalar(
        select(AppInstall).where(
            AppInstall.workspace_id == workspace_id, AppInstall.app_id == app.id
        )
    )
    if row is None:
        row = AppInstall(workspace_id=workspace_id, app_id=app.id, enabled=enabled)
        db.add(row)
    else:
        row.enabled = enabled
    await db.flush()
    return row


async def install_defaults(db: DbSession, workspace_id: int) -> None:
    """What a new workspace starts with. Does not commit."""
    for slug in DEFAULT_APPS:
        await install(db, workspace_id, slug)


async def admit_channel(db: DbSession, workspace_id: int, kind: str) -> JSONResponse | None:
    """Refuse switching a channel on while its app is not installed and enabled.

    Returns the refusal to send, or None when the channel may be switched on.
    """
    slug = slug_of(kind)
    if await is_enabled(db, workspace_id, slug):
        return None
    app = await app_row(db, slug)
    name = str((app.manifest or {}).get("name") or slug) if app is not None else slug
    return envelope_response(
        status_code=status.HTTP_409_CONFLICT,
        code="app_disabled",
        message=f"The {name} app is switched off in this workspace. "
        "Turn it on under Apps first.",
    )
