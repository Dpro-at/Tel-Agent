"""Install, in every existing workspace, the apps it was already using.

Revision ID: 6b2e9d4f1a73
Revises: 1f3c7a2b6d40
Create Date: 2026-09-13

#241 makes `app_installs` mean something: a channel card now refuses to switch its
channel on unless the channel's app is installed and enabled in the workspace. Until
this revision nothing wrote that table, so without a backfill every existing workspace
would find every channel switch refused - including the web chat it was created with.

What counts as already used, per workspace:

- **Web chat, always.** Every workspace was created with a `web` channel row, and new
  ones install the app on creation from now on.
- **Any other channel app whose channel was set up**: a channel row that is switched
  on (`active` or `error`), or that holds credentials. A row alone is not enough -
  opening a channel's card creates a disabled row with nothing in it, and installing
  every app somebody once looked at would make the Installed list meaningless.

**The `apps` rows may not exist yet.** They are written at startup by
`sync_catalogue`, which runs after migrations, so a database that has never been
started by a version with a given channel has no row for it. Missing rows are added
with an empty manifest; the next startup's sync fills them in, and it updates rather
than duplicates (`tests/test_extensions.py`).

The kind-to-app map is frozen here on purpose rather than imported from
`api/extensions/installs.py`: a migration has to keep meaning what it meant on the day
it was written.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "6b2e9d4f1a73"
down_revision = "1f3c7a2b6d40"
branch_labels = None
depends_on = None

# Channel kind -> app slug, for every channel that shipped an app by this revision.
APP_BY_KIND = {
    "web": "web_chat",
    "email": "email",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "messenger": "messenger",
    "instagram": "instagram",
    "discord": "discord",
    "slack": "slack",
    "sms": "sms",
    "teams": "teams",
    "mattermost": "mattermost",
}

apps = sa.table(
    "apps",
    sa.column("id", sa.Integer),
    sa.column("slug", sa.String),
    sa.column("origin", sa.String),
    sa.column("version", sa.String),
    sa.column("manifest", sa.JSON),
)
app_installs = sa.table(
    "app_installs",
    sa.column("workspace_id", sa.Integer),
    sa.column("app_id", sa.Integer),
    sa.column("enabled", sa.Boolean),
    sa.column("settings_json", sa.JSON),
)
workspaces = sa.table("workspaces", sa.column("id", sa.Integer))
channels = sa.table(
    "channels",
    sa.column("workspace_id", sa.Integer),
    sa.column("kind", sa.String),
    sa.column("status", sa.String),
    sa.column("credentials_encrypted", sa.Text),
)


def backfill(bind: sa.engine.Connection) -> None:
    """The whole upgrade, callable on its own so a test can seed rows first."""
    wanted: set[tuple[int, str]] = {
        (workspace_id, "web_chat") for workspace_id in bind.scalars(sa.select(workspaces.c.id))
    }
    used = bind.execute(
        sa.select(channels.c.workspace_id, channels.c.kind).where(
            channels.c.kind.in_(APP_BY_KIND),
            sa.or_(
                channels.c.status != "disabled",
                channels.c.credentials_encrypted.is_not(None),
            ),
        )
    )
    wanted |= {(workspace_id, APP_BY_KIND[kind]) for workspace_id, kind in used}
    if not wanted:
        return

    app_ids = dict(bind.execute(sa.select(apps.c.slug, apps.c.id)).tuples().all())
    for slug in sorted({slug for _, slug in wanted} - set(app_ids)):
        bind.execute(
            apps.insert().values(slug=slug, origin="official", version=None, manifest={})
        )
    app_ids = dict(bind.execute(sa.select(apps.c.slug, apps.c.id)).tuples().all())

    present = set(
        bind.execute(sa.select(app_installs.c.workspace_id, app_installs.c.app_id)).tuples()
    )
    rows = [
        {
            "workspace_id": workspace_id,
            "app_id": app_ids[slug],
            "enabled": True,
            "settings_json": {},
        }
        for workspace_id, slug in sorted(wanted)
        if (workspace_id, app_ids[slug]) not in present
    ]
    if rows:
        bind.execute(app_installs.insert(), rows)


def upgrade() -> None:
    backfill(op.get_bind())


def downgrade() -> None:
    # Before this revision nothing read `app_installs`, so emptying it is the state the
    # previous revision expects. The `apps` rows stay: `sync_catalogue` owns them.
    op.execute(sa.text("DELETE FROM app_installs"))
