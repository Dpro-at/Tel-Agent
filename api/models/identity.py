"""Who signs in, what they sign in to, and what they may do there.

Three tables rather than one, because the interface already needs all three: the
sidebar switches between workspaces, one of them is "shared with you by Sabine", and
`Settings → Users & access` lists five roles. A `user_id` column on every table cannot
express any of that (D-028).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db import Base
from api.models.common import enum_column, utc_now_column

# The five roles `Settings → Users & access` already names and describes.
#
# `invited` is a role rather than a separate `invitations` table: the interface lists an
# invited person in the same table as everybody else, with "Invited" in the role column
# and a "Cancel invite" action. Modelling it as a state of membership is what makes that
# one list instead of two merged for display.
ROLES = ("owner", "admin", "reception", "viewer", "invited")


class User(Base):
    """A person with an account on this installation.

    Sign-in is by **username**, not email address (D-030) — that is what the sign-in
    screen asks for. The email address exists so the six-digit code has somewhere to go,
    and is optional because an installation with no mail server configured cannot send
    one anyway.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Written by D2 (Argon2id). Nullable because a key-only administrator may never set
    # one: §B9 requires that no account ships *with* a password, not that every account
    # has one.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str] = mapped_column(String(12), nullable=False, default="en")
    theme: Mapped[str] = mapped_column(
        enum_column("dark", "light", name="theme"), nullable=False, default="dark"
    )
    created_at: Mapped[dt.datetime] = utc_now_column()

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Workspace(Base):
    """A separate installation in every way that matters to the people using it.

    The interface says so in its own words: *"its own numbers, assistants, catalogue and
    call history. Nothing crosses between them."* Every table that holds data points
    here.
    """

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[dt.datetime] = utc_now_column()

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Membership(Base):
    """One person's access to one workspace.

    A person belongs to several workspaces with a different role in each — which is what
    "shared with you by Sabine" in the workspace switcher means.
    """

    __tablename__ = "memberships"
    __table_args__ = (
        # One membership per person per workspace. Without this, two rows with different
        # roles make "what may this person do here" ambiguous, and whichever row the
        # query happens to return first decides it.
        UniqueConstraint("user_id", "workspace_id", name="user_workspace"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id: Mapped[int] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(enum_column(*ROLES, name="role"), nullable=False)
    created_at: Mapped[dt.datetime] = utc_now_column()

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
