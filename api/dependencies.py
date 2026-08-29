"""The current user, and the rule that a route is protected unless it says otherwise.

Every protected route needs the same check, and a check written twice is a check that
will eventually differ. So it is written once, here.

**Closed by default.** `PUBLIC_PATHS` is the whole list of routes reachable without a
session; anything not on it requires one. A new endpoint added carelessly is therefore
unreachable rather than open, and `tests/test_auth.py` walks the route table to prove
no route escaped. The opposite arrangement — a decorator that marks routes protected —
fails silently the first time somebody forgets it, and nothing reports the omission.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Session, User
from api.security.session import resolve_session, token_from_request

# Reachable without a session, and this is the complete list.
#
# `/api/setup` is here because there is nobody to authenticate as before it runs; it
# defends itself by refusing to run twice (`api/setup.py`).
PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/setup",
        "/api/auth/login",
        "/api/auth/forgot",
        "/api/auth/code/verify",
        "/api/auth/key/challenge",
        "/api/auth/key/verify",
        # Guarded by the reset ticket rather than a session: the whole point is that
        # the caller has no session yet.
        "/api/auth/password/reset",
        # Guarded by the invite token (D-034): the caller has no account worth the
        # name yet. Listed by their patterns so the route-table walk knows they are
        # deliberate; matched at request time by the prefix below.
        "/api/invites/{token}",
        "/api/invites/{token}/accept",
        # The web chat widget - §B14. Public because the address travels in the HTML
        # of the customer's page; guarded by the origin allowlist instead of a
        # session. Listed by pattern so the route-table walk knows it is deliberate.
        "/public/chat/{path}/messages",
        # The reply, streamed. Same guards as the message that asked for it,
        # minus the captcha - that was paid when the message was accepted, and
        # asking twice per exchange doubles the cost to verify nobody new.
        "/public/chat/{path}/stream",
        # The widget: the script a customer pastes and the document it frames. Public
        # for the same reason - both are fetched by a stranger's browser, on a page
        # this installation does not control. Neither reads a session; the page carries
        # its own `frame-ancestors` policy instead (§B14).
        "/embed.js",
        "/widget/{path}",
    }
)

# Parameterised public routes carry a token in the path, so the request-time URL
# never equals its pattern. The prefix is the runtime half of the two entries above.
PUBLIC_PREFIXES: tuple[str, ...] = ("/api/invites/", "/public/chat/", "/widget/")


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def served_paths(app: object) -> set[str]:
    """Every path this application actually serves, flattened.

    `app.routes` is **not** flat. Since FastAPI 0.14x, `include_router` leaves an
    `_IncludedRouter` entry that holds its own `.routes` rather than splicing the
    children into the top level. A walk that only looks at the top level therefore
    sees the four routes registered directly on the app and none of the routers -
    which is precisely how the closed-by-default test came to be inspecting almost
    nothing while reporting green.

    Recursing is the fix, and it is written here beside `PUBLIC_PATHS` rather than in
    a test, because "what does this application serve" is a question the rule itself
    is about.
    """
    found: set[str] = set()

    def walk(routes: object) -> None:
        for route in routes or []:
            path = getattr(route, "path", None)
            if isinstance(path, str):
                found.add(path)

            # An `_IncludedRouter` exposes neither `.path` nor `.routes`; the children
            # hang off `original_router`, and their `.path` is *already* prefixed
            # because `include_router` rewrites them as it copies. A mount is the
            # other shape, and carries `.routes` directly.
            included = getattr(route, "original_router", None)
            if included is not None:
                walk(getattr(included, "routes", None))
                continue

            children = getattr(route, "routes", None)
            if children:
                walk(children)

    walk(getattr(app, "routes", []))
    return found


class Unauthenticated(HTTPException):
    """401, in the one error envelope every failure uses.

    The message never distinguishes "no cookie" from "expired" from "revoked". A
    caller learning which one applies learns something about the state of an account
    they have not proved they own.
    """

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to continue.",
        )


async def get_current_session(request: Request) -> Session:
    """Resolve the cookie to a live session, or refuse."""
    db: DbSession = request.state.db
    session = await resolve_session(db, token_from_request(request))
    if session is None:
        raise Unauthenticated
    return session


async def get_current_user(
    request: Request, session: Annotated[Session, Depends(get_current_session)]
) -> User:
    db: DbSession = request.state.db
    user = await db.get(User, session.user_id)
    if user is None:
        # The row is gone but the session survived — only possible if a delete skipped
        # the cascade. Refuse rather than serve a request for a user who is not there.
        raise Unauthenticated
    return user


CurrentSession = Annotated[Session, Depends(get_current_session)]
CurrentUser = Annotated[User, Depends(get_current_user)]
