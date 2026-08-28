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
from api.security import audit, lockout
from api.security.password import PasswordTooShort, verify_and_upgrade, verify_password
from api.security.session import (
    clear_session_cookie,
    create_session,
    hash_token,
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
        await audit.record(
            db,
            "login_locked" if triggered is not None else "login_failed",
            request=request,
            user_id=user.id if user else None,
            username=payload.username,
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
    await audit.record(
        db, "login_succeeded", request=request, user_id=user.id, username=user.username
    )

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


class ProfileUpdate(BaseModel):
    locale: str | None = Field(default=None, max_length=12)


# The committed tier (locales/README.md): the three languages a release blocks on.
# A community locale becomes selectable here when it is registered, not before.
SUPPORTED_LOCALES = ("en", "de", "ar")


@router.patch("/me", summary="Update the signed-in account", response_model=Me)
async def update_me(request: Request, payload: ProfileUpdate, user: CurrentUser) -> object:
    """The language, and deliberately nothing else yet.

    The email is where reset codes go, so changing it is an account-takeover lever
    from any unlocked browser - it needs the current password, the same way changing
    the password does, and that confirmation flow arrives as its own piece rather
    than as one more field here. There is no display name to edit: `users` has no
    such column, and the screens that would show one (whispers, notes) do not exist.
    """
    db: DbSession = request.state.db

    if payload.locale is not None:
        if payload.locale not in SUPPORTED_LOCALES:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="unsupported_locale",
                message=f"The interface languages are {list(SUPPORTED_LOCALES)}.",
            )
        user.locale = payload.locale

    # Read before the commit expires the row.
    answer = Me(
        id=user.id,
        username=user.username,
        email=user.email,
        locale=payload.locale or user.locale,
        theme=user.theme,
        workspaces=[],
    )
    await db.commit()
    answer.workspaces = await _workspaces_for(db, answer.id)
    return answer


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
    await audit.record(db, "logout", request=request, user_id=session.user_id)
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
    await audit.record(
        db,
        "logout_all",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"sessions_ended": ended},
    )
    return SignedOut(signed_out=False, other_sessions_ended=ended)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class PasswordChanged(BaseModel):
    changed: bool = True
    # Told to the user, as the screen promises: "Every other browser and phone signed
    # in to this account is signed out."
    other_sessions_ended: int = 0


@router.post(
    "/password",
    summary="Change the password, signed in",
    response_model=PasswordChanged,
)
async def change_password(
    request: Request, payload: ChangePasswordRequest, user: CurrentUser
) -> object:
    """The ordinary case - somebody who knows their password picking a new one.

    The current password is required even though the caller already holds a session:
    a session is a browser, not a person, and on a shared machine an open tab must not
    be enough to lock the real owner out by changing the password under them.
    """
    from api.security.passwords_policy import PasswordReused, set_password

    db: DbSession = request.state.db

    if not verify_password(payload.current_password, user.password_hash):
        # Deliberately the same shape as a failed sign-in, and counted like one:
        # this endpoint is a password-guessing oracle for whoever sits at an
        # unlocked browser otherwise.
        ip = request.client.host if request.client else None
        await lockout.record_failure(db, action="password", username=user.username, ip=ip)
        await audit.record(
            db,
            "login_failed",
            request=request,
            user_id=user.id,
            username=user.username,
            details={"via": "change_password"},
        )
        return envelope_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message="That is not the current password.",
        )

    token = token_from_request(request)
    try:
        ended = await set_password(
            db,
            user,
            payload.new_password,
            # The one session that survives is the browser doing the changing.
            keep_session_token_hash=hash_token(token) if token else None,
        )
    except PasswordReused:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="password_reused",
            message="This is one of your recent passwords. Choose a different one.",
        )
    except PasswordTooShort as error:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="password_too_short",
            message=str(error),
        )

    await audit.record(
        db,
        "password_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"sessions_ended": ended},
    )
    return PasswordChanged(other_sessions_ended=ended)


class AuditEntry(BaseModel):
    event: str
    ip: str | None
    user_agent: str | None
    created_at: str
    details: dict | None


@router.get(
    "/events",
    summary="Recent account events, for the settings tab",
    response_model=list[AuditEntry],
)
async def account_events(request: Request, user: CurrentUser) -> list[AuditEntry]:
    """The user's own trail only - who signed in, from where, and what changed.

    Scoped to the requesting account. An owner-wide view across every user belongs to
    the Users & access tab and arrives with the roles work, where "may this person see
    that" has an enforcer.
    """
    from sqlalchemy import select as sa_select

    from api.models import AuthEvent

    db: DbSession = request.state.db
    rows = await db.execute(
        sa_select(AuthEvent)
        .where(AuthEvent.user_id == user.id)
        .order_by(AuthEvent.created_at.desc(), AuthEvent.id.desc())
        .limit(50)
    )
    return [
        AuditEntry(
            event=row.event,
            ip=row.ip,
            user_agent=row.user_agent,
            created_at=row.created_at.isoformat(),
            details=row.details,
        )
        for row in rows.scalars()
    ]
