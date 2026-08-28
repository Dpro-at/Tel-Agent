"""The team in the acting workspace, and creating a new workspace.

The endpoints `Settings → Users & access` and the New-workspace dialog have been
waiting for — over the identity models D-028 put in the first migration.

**What is deliberately not here: invitations.** D-030 settles that further users are
invited by an administrator, and the designed dialog shows an invite *link* — but the
flow needs decisions nothing has recorded: who picks the username the person signs in
with, what the token looks like, how long it lives. An `invited` membership row can
already be listed and cancelled here, because it is a membership state; creating one
arrives with that decision, not before it.

**Two guards repeat below and are the point.** The owner's row is untouchable — the
role matrix says "One person. Cannot be removed", and a workspace whose owner an admin
can demote has no owner in any meaningful sense. And nobody acts on their own row —
an admin who demotes or removes themselves mid-session leaves a workspace nobody may
manage until the owner notices.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel, Membership, User, Workspace
from api.security import audit
from api.security.permissions import WorkspaceContext, require_admin

logger = logging.getLogger("api.workspaces")

router = APIRouter(prefix="/api", tags=["workspaces"])

# What an admin may set a member to. `owner` is absent because ownership moves by
# transfer, which is its own decision with its own confirmations - not a value in a
# role picker. `invited` is absent because an invitation is a pending fact, not a
# rank anybody is assigned to.
ASSIGNABLE_ROLES = ("admin", "reception", "viewer")

# Owner first, viewer last, invitations at the bottom - the order the settings
# screen's fixture always drew.
_ORDER = {"owner": 0, "admin": 1, "reception": 2, "viewer": 3, "invited": 4}


class Member(BaseModel):
    user_id: int
    username: str
    email: str | None
    role: str
    joined_at: str


def _missing() -> object:
    """One answer for "no such member" and "that member is not in this workspace".

    The same rule the notifications route follows: a foreign id must be
    indistinguishable from a missing one, or the endpoint becomes a way of asking
    which user ids exist.
    """
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such member in this workspace.",
    )


async def _member_row(
    db: DbSession, workspace_id: int, user_id: int
) -> tuple[Membership, User] | None:
    row = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == workspace_id, Membership.user_id == user_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


@router.get("/members", response_model=list[Member], summary="The team in this workspace")
async def list_members(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> list[Member]:
    db: DbSession = request.state.db
    rows = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == context.id)
        )
    ).all()
    members = [
        Member(
            user_id=user.id,
            username=user.username,
            email=user.email,
            role=membership.role,
            joined_at=membership.created_at.isoformat(),
        )
        for membership, user in rows
    ]
    members.sort(key=lambda member: (_ORDER.get(member.role, 9), member.username))
    return members


class RoleChange(BaseModel):
    role: str


@router.patch("/members/{user_id}", response_model=Member, summary="Change a member's role")
async def change_role(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    user_id: int,
    payload: RoleChange,
) -> object:
    db: DbSession = request.state.db

    if payload.role not in ASSIGNABLE_ROLES:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_role",
            message=f"A member can be one of {list(ASSIGNABLE_ROLES)}. Ownership is "
            "transferred, not assigned, and an invitation is not a rank.",
        )

    found = await _member_row(db, context.id, user_id)
    if found is None:
        return _missing()
    membership, target = found

    if membership.role == "owner":
        return envelope_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="owner_untouchable",
            message="The owner's role cannot be changed. One person, cannot be removed.",
        )
    if target.id == user.id:
        return envelope_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="own_row",
            message="You cannot change your own role. Ask the owner or another admin.",
        )
    if membership.role == "invited":
        return envelope_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="still_invited",
            message="This invitation has not been accepted yet. Cancel it instead of "
            "assigning it a role.",
        )

    # Read before the commit: a committed row's attributes expire, and refreshing
    # them lazily is not something an async session will do mid-expression.
    was = membership.role
    changed = Member(
        user_id=target.id,
        username=target.username,
        email=target.email,
        role=payload.role,
        joined_at=membership.created_at.isoformat(),
    )
    membership.role = payload.role
    await db.commit()
    await audit.record(
        db,
        "role_changed",
        request=request,
        user_id=changed.user_id,
        username=changed.username,
        details={
            "workspace_id": context.id,
            "from": was,
            "to": payload.role,
            "by_user_id": user.id,
        },
    )
    logger.info(
        "role changed",
        extra={"workspace_id": context.id, "user_id": changed.user_id, "role": payload.role},
    )
    return changed


@router.delete("/members/{user_id}", summary="Remove a member from this workspace")
async def remove_member(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    user_id: int,
) -> Response:
    """Removes the membership, never the person.

    The account may belong to other workspaces, and deleting a user is a different
    action with different consequences (their audit trail, their authored rows). For
    an `invited` row this same delete is "Cancel invite" - one action, because the
    interface lists invited people in the same table as everybody else.
    """
    db: DbSession = request.state.db

    found = await _member_row(db, context.id, user_id)
    if found is None:
        return _missing()
    membership, target = found

    if membership.role == "owner":
        return envelope_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="owner_untouchable",
            message="The owner cannot be removed. One person, cannot be removed.",
        )
    if target.id == user.id:
        return envelope_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="own_row",
            message="You cannot remove yourself. Ask the owner or another admin.",
        )

    was = membership.role
    target_id, target_name = target.id, target.username
    await db.delete(membership)
    await db.commit()
    await audit.record(
        db,
        "member_removed",
        request=request,
        user_id=target_id,
        username=target_name,
        details={"workspace_id": context.id, "role": was, "by_user_id": user.id},
    )
    logger.info(
        "member removed", extra={"workspace_id": context.id, "user_id": target_id, "role": was}
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class NewWorkspace(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # "Everyone in {workspace}" on the dialog: copy the acting workspace's members
    # into the new one with the same roles.
    include_team: bool = False


class WorkspaceCreated(BaseModel):
    id: int
    name: str
    members: int


@router.post(
    "/workspaces",
    response_model=WorkspaceCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
)
async def create_workspace(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewWorkspace,
) -> object:
    """One transaction, same shape as first-run setup: workspace, owner membership,
    and the `web` channel row §B5 decision 6 requires.

    The creator becomes the new workspace's owner regardless of their role here — a
    workspace someone creates and cannot manage is a workspace nobody can manage.
    With `include_team`, everybody else keeps their role, with two exceptions said
    out loud: the acting workspace's owner arrives as `admin`, because a workspace
    has one owner and it is the creator; and `invited` rows are skipped, because an
    invitation was to *that* workspace and copying it would widen what the inviter
    granted.
    """
    db: DbSession = request.state.db

    name = payload.name.strip()
    if len(name) < 2:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="name_too_short",
            message="Give the workspace a name before you continue.",
        )

    # Unique among the creator's own workspaces, per the dialog's copy: "Names are
    # only for your own account, but two identical ones are impossible to tell apart
    # in the switcher." Another account may reuse the name freely.
    clash = await db.scalar(
        select(func.count())
        .select_from(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(
            Membership.user_id == user.id,
            func.lower(Workspace.name) == name.lower(),
        )
    )
    if clash:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="workspace_name_taken",
            message="You already have a workspace with this name.",
        )

    workspace = Workspace(name=name)
    db.add(workspace)
    await db.flush()

    db.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))
    db.add(Channel(workspace_id=workspace.id, kind="web", name="Web chat"))

    copied = 0
    if payload.include_team:
        existing = (
            (
                await db.execute(
                    select(Membership).where(
                        Membership.workspace_id == context.id,
                        Membership.user_id != user.id,
                        Membership.role != "invited",
                    )
                )
            )
            .scalars()
            .all()
        )
        for membership in existing:
            db.add(
                Membership(
                    user_id=membership.user_id,
                    workspace_id=workspace.id,
                    role="admin" if membership.role == "owner" else membership.role,
                )
            )
            copied += 1

    # Read before the commit expires it.
    workspace_id = workspace.id
    acting_user_id, acting_username = user.id, user.username
    await db.commit()
    await audit.record(
        db,
        "workspace_created",
        request=request,
        user_id=acting_user_id,
        username=acting_username,
        details={"workspace_id": workspace_id, "name": name, "members_copied": copied},
    )
    logger.info(
        "workspace created",
        extra={
            "workspace_id": workspace_id,
            "by_user_id": acting_user_id,
            "members_copied": copied,
        },
    )
    # The creator is a member too.
    return WorkspaceCreated(id=workspace_id, name=name, members=copied + 1)
