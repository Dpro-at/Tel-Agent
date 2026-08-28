"""The team in the acting workspace, and creating a new workspace.

The endpoints `Settings → Users & access` and the New-workspace dialog have been
waiting for — over the identity models D-028 put in the first migration.

**Invitations follow D-034.** An admin gives an email address and a role; the server
creates the person as `User` + `Membership(role="invited")` with a provisional
username derived from the email, and a one-time link valid seven days. The invitee
picks their real username and password at accept (`api/routes/invites.py`), which is
when the membership flips to the granted role. "Resend" rotates the token in place —
the old link dies.

**Two guards repeat below and are the point.** The owner's row is untouchable — the
role matrix says "One person. Cannot be removed", and a workspace whose owner an admin
can demote has no owner in any meaningful sense. And nobody acts on their own row —
an admin who demotes or removes themselves mid-session leaves a workspace nobody may
manage until the owner notices.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import (
    INVITE_LIFETIME,
    INVITE_ROLES,
    Channel,
    Invite,
    Membership,
    User,
    Workspace,
)
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
    # Cancelling an invitation must kill its link with it: a membership that is gone
    # while the token still answers would be an invitation nobody sent.
    pending = await db.scalar(
        select(Invite).where(Invite.user_id == target_id, Invite.workspace_id == context.id)
    )
    if pending is not None:
        await db.delete(pending)
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


# --- Invitations - D-034 -------------------------------------------------------


class InviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str


class InviteLink(BaseModel):
    user_id: int
    username: str
    email: str
    role: str
    # The path only. The dashboard builds the absolute link from its own origin,
    # which it knows and the API does not have to guess.
    invite_path: str
    expires_at: str
    # Whether a copy was also queued by email.
    mailed: bool


def _provisional_username(email: str, taken: set[str]) -> str:
    """A name for the team list to show until the person picks their own.

    Derived from the email's local part, reduced to the characters a username can
    carry, and deduplicated with a suffix. It is a placeholder by design — D-034's
    reasoning is that a name is the one thing its owner should choose.
    """
    import re

    stem = re.sub(r"[^a-z0-9_]", "", email.split("@")[0].lower())[:56] or "invited"
    candidate = stem
    suffix = 2
    while candidate in taken:
        candidate = f"{stem}{suffix}"
        suffix += 1
    return candidate


async def _issue_invite(
    db: DbSession,
    request: Request,
    *,
    workspace_id: int,
    workspace_name: str,
    invite: Invite,
    username: str,
    email: str,
) -> InviteLink:
    """Mint a fresh token onto `invite`, queue the email if one can go, and commit.

    The token is returned once and stored only as a hash — a database dump must not
    be a bag of working invite links.
    """
    import secrets

    from api import mail
    from api.jobs.runner import enqueue
    from api.security.session import hash_token

    token = secrets.token_urlsafe(24)
    invite.token_hash = hash_token(token)
    invite.expires_at = dt.datetime.now(dt.UTC) + INVITE_LIFETIME
    expires_at = invite.expires_at.isoformat()
    role = invite.role

    mailed = False
    config = await mail.resolve(db, request.app.state.settings)
    if config.configured:
        # The dashboard's own origin is the one the link must open on. The first
        # configured CORS origin is that origin - the same value the browser is
        # already trusted from.
        origins = request.app.state.settings.cors_origins
        base = origins[0] if origins else ""
        await enqueue(
            db,
            "send_email",
            {
                "to": email,
                "subject": f"You are invited to {workspace_name} on Tel-Agent",
                "body": (
                    f"You have been invited to join {workspace_name} on a Tel-Agent "
                    f"installation.\n\nOpen this link to choose your username and "
                    f"password:\n\n{base}/invite/{token}\n\nThe link works once and "
                    f"expires in seven days."
                ),
            },
        )
        mailed = True

    await db.commit()
    return InviteLink(
        user_id=invite.user_id,
        username=username,
        email=email,
        role=role,
        invite_path=f"/invite/{token}",
        expires_at=expires_at,
        mailed=mailed,
    )


@router.post("/members/invites", response_model=InviteLink, summary="Invite somebody in")
async def create_invite(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: InviteRequest,
) -> object:
    """An email address and a role - the two things the dialog asks - and nothing else.

    A fresh account is always created, even when the address matches an existing one:
    emails here are neither unique nor verified, so attaching a membership to whoever
    typed the same address first would hand access to the wrong person quietly.
    Sharing one account across workspaces is a different feature with a different
    proof of ownership.
    """
    db: DbSession = request.state.db

    if payload.role not in INVITE_ROLES:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_role",
            message=f"An invitation grants one of {list(INVITE_ROLES)}.",
        )
    email = payload.email.strip()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_email",
            message="That does not look like an email address.",
        )

    taken = set((await db.execute(select(User.username))).scalars().all())
    username = _provisional_username(email, taken)

    invited = User(username=username, email=email, password_hash=None)
    db.add(invited)
    await db.flush()
    db.add(Membership(user_id=invited.id, workspace_id=context.id, role="invited"))
    invite = Invite(
        user_id=invited.id,
        workspace_id=context.id,
        role=payload.role,
        token_hash="",  # minted by _issue_invite below
        invited_by=user.id,
        expires_at=dt.datetime.now(dt.UTC),  # replaced by _issue_invite below
    )
    db.add(invite)
    await db.flush()

    invited_id, workspace_name, acting_id = invited.id, context.workspace.name, user.id
    answer = await _issue_invite(
        db,
        request,
        workspace_id=context.id,
        workspace_name=workspace_name,
        invite=invite,
        username=username,
        email=email,
    )
    await audit.record(
        db,
        "invite_created",
        request=request,
        user_id=invited_id,
        username=username,
        details={"workspace_id": context.id, "role": payload.role, "by_user_id": acting_id},
    )
    logger.info(
        "invite created",
        extra={"workspace_id": context.id, "user_id": invited_id, "role": payload.role},
    )
    return answer


@router.post(
    "/members/{user_id}/invite-link",
    response_model=InviteLink,
    summary="Regenerate an invitation link",
)
async def regenerate_invite(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    user_id: int,
) -> object:
    """ "Resend invite": a fresh link, a fresh seven days, and the old link dead.

    Rotation rather than a second row - two working links granting possibly
    different roles would race, and whichever was clicked last would win silently.
    """
    db: DbSession = request.state.db

    found = await _member_row(db, context.id, user_id)
    if found is None:
        return _missing()
    membership, target = found
    if membership.role != "invited":
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="already_accepted",
            message="This person has already accepted. There is no invitation to resend.",
        )

    invite = await db.scalar(
        select(Invite).where(Invite.user_id == user_id, Invite.workspace_id == context.id)
    )
    if invite is None:
        return _missing()

    target_name = target.username
    target_email = target.email or ""
    if not target_email:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="no_email_on_account",
            message="This invitation has no email address to send to.",
        )
    return await _issue_invite(
        db,
        request,
        workspace_id=context.id,
        workspace_name=context.workspace.name,
        invite=invite,
        username=target_name,
        email=target_email,
    )


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
