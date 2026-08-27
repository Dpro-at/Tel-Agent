"""The settings store — P3.

CLAUDE.md states the rule this table exists to satisfy: *"Never build a settings screen
that writes to `.env`."* An eleven-tab settings screen already exists, and a `.env` file
cannot serve it — editing one needs shell access, a restart, and a person who knows
what a dotfile is. Settings that a user changes from a browser live here.

**Two scopes, one table.** A row with `workspace_id = NULL` belongs to the installation
(the mail server, the hostname); a row with one belongs to that workspace (its opening
hours, its recording policy). The same lookup serves both, and `get()` falls back from
workspace to installation so a workspace inherits until it overrides — the behaviour
every settings screen with a "use the default" checkbox needs.

**`secret` decides how the value is stored and shown.** A secret is written through the
encrypted column and returned masked; anything else is plain. Which one a key is, is
declared once in `api/settings/registry.py` rather than at each call site — the same
reasoning as `EncryptedStr`: a decision repeated at call sites is a decision forgotten
at one of them.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import utc_now_column
from api.models.encrypted import EncryptedStr


class Setting(Base):
    """One stored value, for the installation or for one workspace."""

    __tablename__ = "settings"
    __table_args__ = (
        # One row per key per scope. Without this, two rows disagree and whichever the
        # query returns first silently wins - the class of bug that is impossible to
        # reproduce because it depends on insertion order.
        UniqueConstraint("workspace_id", "key", name="scope_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # NULL means the installation itself. Not a sentinel id: a foreign key cannot point
    # at a workspace that does not exist, and "no workspace" is exactly what NULL says.
    workspace_id: Mapped[int | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    # Exactly one of these carries the value. Two columns rather than one because the
    # encrypted type has to be declared on the column: a single column cannot be
    # sometimes-encrypted, and a flag that says "this one is encrypted" is a flag that
    # will one day disagree with the bytes beside it.
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_value: Mapped[str | None] = mapped_column(EncryptedStr, nullable=True)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: dt.datetime.now(dt.UTC)
    )
    created_at: Mapped[dt.datetime] = utc_now_column()
