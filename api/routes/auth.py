"""Sign in, sign out, and who am I.

The endpoints `web/app/[locale]/login` has been waiting for. The rest of the flow —
the six-digit code, the SSH key, the reset — arrives with D9 to D11; these three are
what make the dashboard reachable.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.config import Settings
from api.dependencies import CurrentSession, CurrentUser
from api.errors import envelope_response
from api.models import Membership, User, Workspace
from api.security import lockout
from api.security.password import verify_and_upgrade
from api.security.session import (
    clear_session_cookie,
    create_session,
    revoke_all_other_sessions,
    revoke_session,
    set_session_cookie,
    token_from_request,
)

logger = logging.getLogger("api.auth")

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# One message for a wrong password and for an account that does not exist.
#
# Anything that distinguishes them turns the login form into a way of asking "does
# this person have an account here", which on a small business's own server is a
# meaningful thing to learn. The reset screen already says the same thing for the same
# reason: "The screen says the same thing whether or not the account exists."
WRONG_CREDENTIALS = "That username and password do not match."


def _rate_limited(lock: lockout.Lock) -> object:
    """The one shape a throttled response takes.

    `locked_until` is what the sign-in screen renders as "unlocks at 11:19", so the
    time is sent rather than only a duration - the browser does the formatting, in the
    user's locale and timezone, which the server does not know.
    """
    return envelope_response(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        code="rate_limited",
        message="Too many attempts. Try again shortly.",
        details=[
            {
                "locked_until": lock.locked_until.isoformat(),
                "seconds_remaining": lock.seconds_remaining,
            }
        ],
    )


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class WorkspaceSummary(BaseModel):
    id: int
    name: str
    role: str


class Me(BaseModel):
    """Who is signed in, and what they can reach."""

    id: int
    username: str
    email: str | None
    locale: str
    theme: str
    workspaces: list[WorkspaceSummary]


class SignedOut(BaseModel):
    signed_out: bool = True
    other_sessions_ended: int = 0


async def _workspaces_for(db: DbSession, user_id: int) -> list[WorkspaceSummary]:
    rows = await db.execute(
        select(Workspace.id, Workspace.name, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id)
        .order_by(Workspace.id)
    )
    return [WorkspaceSummary(id=id_, name=name, role=role) for id_, name, role in rows]


@router.post(
    "/login",
    summary="Sign in with a username and password",
    response_model=Me,
    responses={401: {"description": "Wrong credentials, or no such account"}},
)
async def login(request: Request, payload: LoginRequest, response: Response) -> object:
    db: DbSession = request.state.db
    settings: Settings = request.app.state.settings
    ip = request.client.host if request.client else None

    # Checked before the password is even looked at. Verifying first would let an
    # attacker keep testing guesses against a locked account and learn from the timing
    # which ones were close, which is the whole thing the lock exists to stop.
    locked = await lockout.check(db, action="login", username=payload.username, ip=ip)
    if locked is not None:
        return _rate_limited(locked)

    user = await db.scalar(select(User).where(User.username == payload.username))

    # `verify_and_upgrade` is called even when there is no such user, against a hash
    # that cannot match. Returning early would make a missing account measurably faster
    # than a wrong password, and that timing difference is the same disclosure the
    # shared error message exists to prevent.
    ok, replacement = verify_and_upgrade(payload.password, user.password_hash if user else None)

    if not ok or user is None:
        # Counted whether or not the account exists. Counting only real accounts would
        # make an unknown username cheap to test, which is a way of asking who has one.
        triggered = await lockout.record_failure(
            db, action="login", username=payload.username, ip=ip
        )
        logger.info(
            "sign-in refused",
            extra={"username": payload.username, "reason": "credentials"},
        )
        # The failure that trips the lock says so in the same response. The sign-in
        # screen's blocked state shows "that password is wrong" *and* the unlock time
        # together, so answering 401 now and 429 only on the next attempt would leave
        # the screen unable to render a state it was designed for.
        if triggered is not None:
            return _rate_limited(triggered)
        return envelope_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message=WRONG_CREDENTIALS,
        )

    await lockout.clear(db, action="login", username=payload.username, ip=ip)

    # The parameters have been raised since this hash was made. Re-store it now, while
    # the plaintext is in hand — this is the only moment it can be done.
    if replacement is not None:
        user.password_hash = replacement

    token = await create_session(db, user, user_agent=request.headers.get("user-agent"), ip=ip)
    set_session_cookie(response, token, secure=not settings.debug)
    logger.info("signed in", extra={"user_id": user.id})

    return Me(
        id=user.id,
        username=user.username,
        email=user.email,
        locale=user.locale,
        theme=user.theme,
        workspaces=await _workspaces_for(db, user.id),
    )


@router.get("/me", summary="The signed-in user and their workspaces", response_model=Me)
async def me(request: Request, user: CurrentUser) -> Me:
    db: DbSession = request.state.db
    return Me(
        id=user.id,
        username=user.username,
        email=user.email,
        locale=user.locale,
        theme=user.theme,
        workspaces=await _workspaces_for(db, user.id),
    )


@router.post("/logout", summary="End this session", response_model=SignedOut)
async def logout(request: Request, response: Response, session: CurrentSession) -> SignedOut:
    """Delete the row, then clear the cookie.

    In that order and both of them. Clearing the cookie alone leaves a session that is
    still valid to anyone who copied it — which is exactly the case a shared machine
    produces.
    """
    db: DbSession = request.state.db
    settings: Settings = request.app.state.settings
    token = token_from_request(request)
    if token:
        await revoke_session(db, token)
    clear_session_cookie(response, secure=not settings.debug)
    logger.info("signed out", extra={"user_id": session.user_id})
    return SignedOut()


@router.post(
    "/logout-all",
    summary="End every other session on this account",
    response_model=SignedOut,
)
async def logout_everywhere(request: Request, user: CurrentUser) -> SignedOut:
    """Ends the others and keeps this one.

    An owner who suspects a forgotten tablet in the back office should not have to sign
    themselves out to deal with it.
    """
    db: DbSession = request.state.db
    token = token_from_request(request)
    ended = await revoke_all_other_sessions(db, user.id, token) if token else 0
    logger.info("signed out everywhere", extra={"user_id": user.id, "ended": ended})
    return SignedOut(signed_out=False, other_sessions_ended=ended)
