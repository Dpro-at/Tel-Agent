"""The extension registry — D-031.

Two tables: what exists, and what a given workspace has turned on. The core registers
itself through the same contract, so `agent_core`, `database` and `web_chat` are rows
here rather than privileged code paths, exactly as the `apps` screen already shows them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk

# The four origins the `apps` catalogue already distinguishes.
#
# `mcp` is not a kind of package: those entries have no code of their own at all. The
# customer points Tel-Agent at an MCP server and its tools become callable, which is
# why the column is `origin` rather than `source` — it says where the behaviour comes
# from, not where a file was downloaded from.
ORIGINS = ("official", "community", "planned", "mcp")


class App(Base):
    """An extension this installation knows about."""

    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The stable identifier. `web_chat` stays `web_chat` across renames of its title,
    # because migrations, manifests and the catalogue all refer to it by this.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    origin: Mapped[str] = mapped_column(
        enum_column(*ORIGINS, name="app_origin"), nullable=False
    )
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The declared contract: permission scopes, UI slots, hooks, migration namespace.
    # JSON rather than columns because the manifest is the extension's shape, not ours —
    # a new field in a manifest must not require a migration of this table.
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = utc_now_column()

    installations: Mapped[list[AppInstall]] = relationship(
        back_populates="app", cascade="all, delete-orphan"
    )


class AppInstall(Base):
    """One workspace's installation of one app.

    Per workspace, not per installation: a workspace is a separate installation in every
    way that matters (D-028), so two workspaces on the same machine can run different
    sets of apps with different settings.
    """

    __tablename__ = "app_installs"
    __table_args__ = (UniqueConstraint("workspace_id", "app_id", name="workspace_app"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    app_id: Mapped[int] = mapped_column(
        ForeignKey("apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Installed and disabled is a real state, and a different one from not installed:
    # it keeps the settings while the app stops running.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = utc_now_column()

    app: Mapped[App] = relationship(back_populates="installations")
