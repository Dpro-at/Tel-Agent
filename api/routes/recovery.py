"""Getting back in: the code, the key, and the new password.

Three ways this installation can prove somebody is who they say, in the order the
screens present them:

* **`forgot`** asks for a username and sends a six-digit code — *if* this installation
  can send email at all. When it cannot, it says so and points at a command on the
  machine, which is the honest answer on software that ships without a mail server.
* **`code`** takes the six digits, and hands back a short-lived reset session. The same
  endpoint serves the second factor (D-030), which is why `purpose` is explicit.
* **`key`** is the alternative that needs no password and no mail: a challenge signed
  locally with an SSH key an administrator registered.

**Nothing here reveals whether an account exists.** The `forgot` screen states the rule
in its own copy — *"The screen says the same thing whether or not the account exists.
Telling you would tell anyone else who asked."* — and it applies to the key flow too:
an unknown username still gets a challenge.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api import mail
from api.config import Settings
from api.errors import envelope_response
from api.jobs.runner import enqueue
from api.models import User
from api.security import audit, codes, lockout, ssh_keys
from api.security.password import PasswordTooShort
from api.security.passwords_policy import PasswordReused, set_password
from api.security.session import create_session, set_session_cookie
from api.security.ssh_keys import SshKeygenMissing

logger = logging.getLogger("api.auth")

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# The reset ticket. Issued once a code or a key has been accepted, and spent by
# `/password/reset`. A cookie rather than a body value so it is `HttpOnly` like the
# session — a token that authorises a password change is worth as much as a session.
RESET_COOKIE = "telagent_reset"
RESET_TTL_SECONDS = 15 * 60

# Held in the process rather than the database. It lives fifteen minutes, a restart
# losing one costs the person one more click on "send a new code", and it keeps a
# fourth table out of the schema for something this short-lived.
_reset_tickets: dict[str, int] = {}


class ForgotRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ForgotResponse(BaseModel):
    """Identical whether or not the account exists.

    `delivery` describes the *installation*, not the account: whether this machine can
    send email at all. That leaks nothing about who has an account here, and it is what
    lets the screen choose between "check your email" and its `no_mail` state.
    """

    delivery: str  # "email" | "unavailable"


class CodeRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=4, max_length=12)
    purpose: str = "reset"


class ChallengeRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ChallengeResponse(BaseModel):
    challenge: str
    namespace: str
    command: str
    expires_in_seconds: int


class KeyVerifyRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    challenge: str = Field(min_length=1, max_length=80)
    signature: str = Field(min_length=1, max_length=ssh_keys.MAX_SIGNATURE_BYTES)


class ResetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class Accepted(BaseModel):
    ok: bool = True


async def _user(db: DbSession, username: str) -> User | None:
    return await db.scalar(select(User).where(User.username == username))


def _issue_ticket(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    _reset_tickets[token] = user_id
    return token


def _spend_ticket(token: str | None) -> int | None:
    """Single use: the ticket is removed as it is read."""
    return _reset_tickets.pop(token, None) if token else None


@router.post("/forgot", summary="Ask for a sign-in code", response_model=ForgotResponse)
async def forgot(request: Request, payload: ForgotRequest) -> object:
    db: DbSession = request.state.db
    settings: Settings = request.app.state.settings
    ip = request.client.host if request.client else None

    locked = await lockout.check(db, action="forgot", username=payload.username, ip=ip)
    if locked is not None:
        return envelope_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="rate_limited",
            message="Too many requests. Try again shortly.",
            details=[
                {
                    "locked_until": locked.locked_until.isoformat(),
                    "seconds_remaining": locked.seconds_remaining,
                }
            ],
        )

    mail_config = await mail.resolve(db, settings)
    if not mail_config.configured:
        # Not an error. The screen has a state for exactly this, and pretending a
        # message is on its way would send somebody to wait for one that never arrives.
        return ForgotResponse(delivery="unavailable")

    # Counted before the account is looked up, so a request for a username that exists
    # costs the same as one that does not.
    await lockout.record_failure(db, action="forgot", username=payload.username, ip=ip)

    user = await _user(db, payload.username)
    if user is not None and user.email:
        await audit.record(
            db,
            "recovery_code_requested",
            request=request,
            user_id=user.id,
            username=user.username,
        )
        code = await codes.issue(db, user, "reset")
        # Queued, not sent here. Delivery is somebody else's server: it can be slow,
        # briefly down, or rate limiting us, and none of that should decide how long
        # this request takes or whether it succeeds. The runner's backoff turns a mail
        # server that is down for a minute into a code that arrives a minute late
        # rather than one that is silently lost.
        await enqueue(
            db,
            "send_email",
            {
                "to": user.email,
                "subject": "Your Tel-Agent sign-in code",
                "body": mail.reset_code_body(
                    code, int(codes.CODE_LIFETIME.total_seconds() // 60)
                ),
            },
        )
        await db.commit()

    # The same answer either way, and deliberately not "sent" versus "not sent".
    return ForgotResponse(delivery="email")


@router.post("/code/verify", summary="Check a six-digit code", response_model=Accepted)
async def verify_code(request: Request, payload: CodeRequest, response: Response) -> object:
    db: DbSession = request.state.db
    settings: Settings = request.app.state.settings
    ip = request.client.host if request.client else None

    if payload.purpose not in ("reset", "second_factor"):
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            message="Unknown code purpose.",
        )

    user = await _user(db, payload.username)
    if user is None:
        # Counted and answered exactly as a wrong code would be.
        await lockout.record_failure(db, action="code", username=payload.username, ip=ip)
        return _code_refused(codes.CodeResult.WRONG, 0)

    result, remaining = await codes.verify(db, user, payload.purpose, payload.code)
    if result != codes.CodeResult.OK:
        await lockout.record_failure(db, action="code", username=payload.username, ip=ip)
        return _code_refused(result, remaining)

    await lockout.clear(db, action="code", username=payload.username, ip=ip)

    if payload.purpose == "second_factor":
        # The factor is satisfied: this is a sign-in, so it ends in a session.
        token = await create_session(
            db, user, user_agent=request.headers.get("user-agent"), ip=ip
        )
        set_session_cookie(response, token, secure=not settings.debug)
        await audit.record(
            db,
            "second_factor_used",
            request=request,
            user_id=user.id,
            username=user.username,
        )
        return Accepted()

    # A reset: hand back a ticket that authorises exactly one password change.
    response.set_cookie(
        RESET_COOKIE,
        _issue_ticket(user.id),
        max_age=RESET_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/",
    )
    return Accepted()


def _code_refused(result: str, remaining: int) -> object:
    """The screen distinguishes wrong from expired; both are refusals."""
    if result == codes.CodeResult.EXPIRED:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="code_expired",
            message="This code has expired. Ask for a new one.",
        )
    if result == codes.CodeResult.EXHAUSTED:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="code_expired",
            message="Too many wrong attempts. Ask for a new code.",
        )
    return envelope_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="code_wrong",
        message="That code does not match.",
        details=[{"attempts_left": remaining}],
    )


@router.post(
    "/key/challenge",
    summary="Mint a challenge to sign with an SSH key",
    response_model=ChallengeResponse,
)
async def key_challenge(request: Request, payload: ChallengeRequest) -> ChallengeResponse:
    """Issued for any username, existing or not.

    Refusing unknown usernames here would turn this endpoint into a way of asking who
    has an account — the same disclosure the sign-in and reset screens both avoid.
    """
    db: DbSession = request.state.db
    challenge = await ssh_keys.mint_challenge(db, payload.username)
    return ChallengeResponse(
        challenge=challenge,
        namespace=ssh_keys.NAMESPACE,
        command=f"ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n {ssh_keys.NAMESPACE}",
        expires_in_seconds=int(ssh_keys.CHALLENGE_LIFETIME.total_seconds()),
    )


@router.post("/key/verify", summary="Sign in with a signed challenge", response_model=Accepted)
async def key_verify(request: Request, payload: KeyVerifyRequest, response: Response) -> object:
    db: DbSession = request.state.db
    settings: Settings = request.app.state.settings
    ip = request.client.host if request.client else None

    locked = await lockout.check(db, action="key", username=payload.username, ip=ip)
    if locked is not None:
        return envelope_response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="rate_limited",
            message="Too many attempts. Try again shortly.",
            details=[{"seconds_remaining": locked.seconds_remaining}],
        )

    # Consumed whether or not it verifies: an offered challenge is spent, so a captured
    # signature cannot be replayed and a failed attempt cannot be retried against it.
    live = await ssh_keys.take_challenge(db, payload.username, payload.challenge)
    user = await _user(db, payload.username)

    matched = None
    if live and user is not None:
        try:
            matched = await ssh_keys.find_matching_key(
                db, user.id, message=payload.challenge, signature=payload.signature
            )
        except SshKeygenMissing as error:
            logger.error("key sign-in unavailable: %s", error)
            return envelope_response(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="key_signin_unavailable",
                message="This installation cannot verify SSH signatures.",
            )

    if matched is None:
        await lockout.record_failure(db, action="key", username=payload.username, ip=ip)
        await audit.record(
            db,
            "key_sign_in_failed",
            request=request,
            user_id=user.id if user else None,
            username=payload.username,
        )
        return envelope_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message="That signature does not match any key registered to this account.",
        )

    await lockout.clear(db, action="key", username=payload.username, ip=ip)
    assert user is not None  # noqa: S101 - matched implies user
    token = await create_session(db, user, user_agent=request.headers.get("user-agent"), ip=ip)
    set_session_cookie(response, token, secure=not settings.debug)
    return Accepted()


@router.post(
    "/password/reset",
    summary="Set a new password with a reset ticket",
    response_model=Accepted,
)
async def reset_password(request: Request, payload: ResetRequest, response: Response) -> object:
    db: DbSession = request.state.db
    settings: Settings = request.app.state.settings

    user_id = _spend_ticket(request.cookies.get(RESET_COOKIE))
    if user_id is None:
        return envelope_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message="Prove who you are before changing a password.",
        )

    user = await db.get(User, user_id)
    if user is None:
        return envelope_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="unauthenticated",
            message="Prove who you are before changing a password.",
        )

    try:
        # Every session is ended, including any this browser had: the screen says
        # "Every other browser and phone signed in to this account is signed out", and
        # the person is about to sign in with the new password anyway.
        await set_password(db, user, payload.password)
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
        db, "password_reset", request=request, user_id=user.id, username=user.username
    )
    response.delete_cookie(RESET_COOKIE, path="/", secure=not settings.debug)
    return Accepted()
