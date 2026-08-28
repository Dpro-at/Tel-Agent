"""Accepting an invitation — the public half of D-034.

These two routes are reachable without a session, because the whole point is that
the caller has no account worth the name yet. The token is the secret: 24 random
bytes, single-use, seven days, stored only as a hash. An unknown token answers the
same as a missing one; an expired or used one is told apart honestly, because the
person holding a dead link needs to know to ask for a new one, and a token is not
guessable enough for that distinction to leak anything.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.config import Settings
from api.errors import envelope_response
from api.models import Invite, Membership, User, Workspace
from api.routes.auth import Me, _workspaces_for
from api.security import audit
from api.security.password import PasswordTooShort
from api.security.session import create_session, hash_token, set_session_cookie

logger = logging.getLogger("api.invites")

router = APIRouter(prefix="/api/invites", tags=["invitations"])


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _invalid() -> object:
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="invite_invalid",
        message="This invitation link is not valid.",
    )


async def _load(db: DbSession, token: str) -> Invite | object:
    invite = await db.scalar(select(Invite).where(Invite.token_hash == hash_token(token)))
    if invite is None:
        return _invalid()
    if invite.accepted_at is not None:
        return envelope_response(
            status_code=status.HTTP_410_GONE,
            code="invite_used",
            message="This invitation has already been accepted.",
        )
    if _aware(invite.expires_at) < dt.datetime.now(dt.UTC):
        return envelope_response(
            status_code=status.HTTP_410_GONE,
            code="invite_expired",
            message="This invitation has expired. Ask for a new link.",
        )
    return invite


class InvitePreview(BaseModel):
    workspace: str
    role: str
    email: str | None
    # The placeholder the admin's list shows; the person replaces it below.
    suggested_username: str
    expires_at: str


@router.get("/{token}", response_model=InvitePreview, summary="What this link opens")
async def read_invite(request: Request, token: str) -> object:
    db: DbSession = request.state.db
    invite = await _load(db, token)
    if not isinstance(invite, Invite):
        return invite

    user = await db.get(User, invite.user_id)
    workspace = await db.get(Workspace, invite.workspace_id)
    if user is None or workspace is None:  # the workspace or account was deleted meanwhile
        return _invalid()
    return InvitePreview(
        workspace=workspace.name,
        role=invite.role,
        email=user.email,
        suggested_username=user.username,
        expires_at=_aware(invite.expires_at).isoformat(),
    )


class AcceptRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


@router.post(
    "/{token}/accept", response_model=Me, summary="Choose a name, set a password, get in"
)
async def accept_invite(
    request: Request, response: Response, token: str, payload: AcceptRequest
) -> object:
    """The moment the invitation becomes access.

    The invitee picks the username - D-034's reasoning: a name is the one thing its
    owner should choose. The password goes through the same policy as everywhere
    (length, history), the membership flips from `invited` to the granted role, and
    the reply is a signed-in session - a person who just chose a password should not
    be asked to type it again ten seconds later.
    """
    from api.security.passwords_policy import set_password

    db: DbSession = request.state.db
    settings: Settings = request.app.state.settings

    invite = await _load(db, token)
    if not isinstance(invite, Invite):
        return invite

    username = payload.username.strip()
    if len(username) < 2 or any(ch.isspace() for ch in username):
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_username",
            message="A username needs at least two characters and no spaces.",
        )

    user = await db.get(User, invite.user_id)
    membership = await db.scalar(
        select(Membership).where(
            Membership.user_id == invite.user_id,
            Membership.workspace_id == invite.workspace_id,
        )
    )
    if user is None or membership is None:
        return _invalid()

    clash = await db.scalar(select(User).where(User.username == username, User.id != user.id))
    if clash is not None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="username_taken",
            message="That username is taken. Pick another.",
        )

    try:
        await set_password(db, user, payload.password)
    except PasswordTooShort as error:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="password_too_short",
            message=str(error),
        )

    user.username = username
    membership.role = invite.role
    invite.accepted_at = dt.datetime.now(dt.UTC)

    # Read before the commit expires the rows.
    answer = Me(
        id=user.id,
        username=username,
        email=user.email,
        locale=user.locale,
        theme=user.theme,
        workspaces=[],
    )
    granted, workspace_id = invite.role, invite.workspace_id

    session_token = await create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    await db.commit()
    set_session_cookie(response, session_token, secure=not settings.debug)

    await audit.record(
        db,
        "invite_accepted",
        request=request,
        user_id=answer.id,
        username=username,
        details={"workspace_id": workspace_id, "role": granted},
    )
    logger.info(
        "invite accepted",
        extra={"workspace_id": workspace_id, "user_id": answer.id, "role": granted},
    )
    answer.workspaces = await _workspaces_for(db, answer.id)
    return answer
