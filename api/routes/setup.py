"""First run — the only endpoint that creates an account out of nothing.

`api/setup.py` has held `create_first_administrator` since the foundations, and
`api/dependencies.py` has listed `/api/setup` among the paths reachable without a
session since the same day. Nothing served it. So a fresh installation had no way in
at all: `scripts/seed.py` is development-only, and the alternative was a database
client. This module is that missing route, and nothing else.

**Why it may run without a session.** There is nobody to authenticate as. That is not
a hole to be plugged with a token from somewhere — a token would have to be printed by
the server and read from a log, which is a worse first-run experience and no safer
against anybody who can already read the logs. It defends itself the only way that
works: `create_first_administrator` refuses to run twice, and the check is inside the
transaction that does the writing, so two simultaneous requests cannot both win.

**What it does not defend against, said plainly.** Between the first start and the
first account, whoever reaches the port becomes the owner. That window is real, and it
is closed by not exposing the port — §B9's supported paths are a private network, a
VPN, or a reverse proxy. It is not closed by this file, and pretending otherwise here
would be the more dangerous documentation.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.errors import envelope_response
from api.security import audit
from api.security.password import MINIMUM_LENGTH, PasswordTooShort
from api.security.session import create_session, set_session_cookie
from api.setup import AlreadySetUp, create_first_administrator, is_set_up

logger = logging.getLogger("api.setup")

router = APIRouter(prefix="/api/setup", tags=["setup"])


class SetupState(BaseModel):
    needed: bool


class FirstRunRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str
    workspace_name: str = Field(min_length=1, max_length=120)
    email: str | None = None
    # Only the three the interface is translated into (§A4). A locale nothing renders
    # would leave the first screen in a language the account holder did not choose.
    locale: Annotated[str, Field(pattern="^(en|de|ar)$")] = "en"


class FirstRunDone(BaseModel):
    """What was created, in the words the sidebar will use for them."""

    username: str
    workspace: str
    workspace_id: int


@router.get("", summary="Whether this installation still needs its first account")
async def setup_state(request: Request) -> SetupState:
    """Answered without a session, because the answer decides whether one is possible.

    This discloses that an installation is fresh, and that is worth stating rather than
    hiding: the fact is already discoverable by anybody who can POST here, and the sign
    in screen needs it to send a first-time operator somewhere that can work instead of
    to a password box no password satisfies.
    """
    db: DbSession = request.state.db
    return SetupState(needed=not await is_set_up(db))


@router.post(
    "",
    summary="Create the first account, its workspace, and sign it in",
    response_model=FirstRunDone,
    status_code=status.HTTP_201_CREATED,
)
async def first_run(request: Request, payload: FirstRunRequest, response: Response) -> object:
    """One transaction, then a session — the operator lands signed in.

    Signing in here rather than redirecting to the sign-in screen is not a convenience:
    somebody who has just chosen a password and is immediately asked for it again
    reasonably wonders whether the first step worked.
    """
    db: DbSession = request.state.db

    try:
        created = await create_first_administrator(
            db,
            username=payload.username,
            password=payload.password,
            workspace_name=payload.workspace_name,
            email=payload.email or None,
            locale=payload.locale,
        )
    except AlreadySetUp:
        # Not 403: nothing about the caller is wrong, the installation is simply past
        # this point. The screen sends them to sign in.
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="already_set_up",
            message="This installation already has an account. Sign in instead.",
        )
    except PasswordTooShort:
        # Answered rather than raised. §B9 puts the minimum at twelve characters, and
        # somebody who reads that as a 500 concludes the software is broken rather
        # than that their password is short.
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="password_too_short",
            message=f"A password must be at least {MINIMUM_LENGTH} characters. "
            "A sentence you can remember beats a short string you cannot.",
        )

    token = await create_session(
        db,
        created.user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    set_session_cookie(response, token, secure=not request.app.state.settings.debug)

    # Recorded after the account exists, because it is recorded *against* it. An
    # installation whose log does not open with this line was set up some other way.
    await audit.record(
        db,
        "installation_created",
        request=request,
        user_id=created.user.id,
        username=created.user.username,
        details={"workspace_id": created.workspace.id},
    )
    await db.commit()

    logger.info(
        "installation set up",
        extra={"user_id": created.user.id, "workspace_id": created.workspace.id},
    )
    return FirstRunDone(
        username=created.user.username,
        workspace=created.workspace.name,
        workspace_id=created.workspace.id,
    )
