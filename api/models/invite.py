"""Invitations — D-034.

One row per pending membership, not a separate list the screen would have to merge:
the person already exists as `User` + `Membership(role="invited")` (identity.py's own
commitment), and this row carries what the membership cannot — the role they were
*granted*, the one-time link that turns the invitation into access, and how long it
lives. "Resend invite" rotates the token in this same row; the old link dies.

The token itself is never stored — only its hash, the same discipline sessions
follow. A database dump must not be a bag of working invite links.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk

# What an invitation can grant. `owner` is absent for the same reason it is absent
# from the role picker - ownership moves by transfer - and `invited` is the state,
# not a grant.
INVITE_ROLES = ("admin", "reception", "viewer")

# Long enough to survive a weekend and a holiday; short enough that a forgotten
# link in an inbox is not a standing door.
INVITE_LIFETIME = dt.timedelta(days=7)


class Invite(Base):
    """The one-time link behind one pending membership."""

    __tablename__ = "invites"
    __table_args__ = (
        # One live invitation per person per workspace. Two rows would mean two
        # working links granting possibly different roles, and whichever was clicked
        # last would win silently.
        UniqueConstraint("user_id", "workspace_id", name="invite_user_workspace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[int] = workspace_fk()
    # The role acceptance grants. The membership says `invited` until then.
    role: Mapped[str] = mapped_column(
        enum_column(*INVITE_ROLES, name="invite_role"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Who asked them in. SET NULL rather than CASCADE: the invitation outlives the
    # inviter's account, because the invited person's access should not vanish with it.
    invited_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = utc_now_column()
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Kept after acceptance rather than deleted: "who invited this person, when, and
    # when they accepted" is the kind of question that gets asked months later.
    accepted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
