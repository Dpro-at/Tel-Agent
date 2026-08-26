"""One database session per request, and the closed-by-default authentication gate.

Two jobs in one middleware because they share a resource: the gate has to read the
`sessions` table before any route runs, and opening a second connection to do that
would double the pool usage of every request.

`request.state.db` is therefore the request's session, opened here and closed here.
`get_session` in `api/main.py` hands the same object to routes rather than opening
another, so a request is one connection whatever it touches.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.db import session_scope
from api.dependencies import is_public
from api.errors import envelope_response
from api.security.session import resolve_session, token_from_request

logger = logging.getLogger("api.auth")


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Open the session, then refuse anything that is not public and not signed in."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        async with session_scope(request.app.state.sessionmaker) as db:
            request.state.db = db
            request.state.session = None
            request.state.user_id = None

            # CORS preflight carries no cookie by design; refusing it here would break
            # every cross-origin request from the dashboard before it is even made.
            if request.method == "OPTIONS":
                return await call_next(request)

            if not is_public(request.url.path):
                session = await resolve_session(db, token_from_request(request))
                if session is None:
                    logger.info(
                        "unauthenticated request refused",
                        extra={"method": request.method, "path": request.url.path},
                    )
                    return envelope_response(
                        status_code=401,
                        code="unauthenticated",
                        message="Sign in to continue.",
                    )
                request.state.session = session
                request.state.user_id = session.user_id

            return await call_next(request)
