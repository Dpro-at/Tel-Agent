"""Closed-by-default, extended to WebSocket upgrades.

`AuthenticationMiddleware` is built on `BaseHTTPMiddleware`, which never sees a
websocket scope — so without this gate, every websocket route added from now on would
be reachable without a session, silently, and nothing in the route-table test would
notice. This is the same hole the CSRF middleware closed for origins, closed for
authentication.

The gate refuses **before accepting**: the ASGI close message on an unaccepted socket
becomes an HTTP 403 on the handshake, so an unauthenticated client never gets a socket
at all rather than getting one that is immediately shut.

What the gate proves is that a live session existed at the moment of the upgrade. What
it cannot do is keep watching — that is `watch_session` in `api/security/websocket.py`,
which the route itself runs so that deleting the session closes the open socket.
"""

from __future__ import annotations

from http import cookies as http_cookies
from typing import TYPE_CHECKING

from api.db import session_scope
from api.dependencies import is_public
from api.security.session import COOKIE_NAME, resolve_session
from api.security.websocket import WS_POLICY_VIOLATION

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


def _cookie(scope: Scope, name: str) -> str | None:
    """The named cookie from the raw handshake headers.

    Parsed with the standard library rather than by hand: cookie header syntax has
    enough corner cases (quoting, embedded equals signs) that a `split(";")` version
    is a bug that waits for the first unusual browser.
    """
    for key, value in scope.get("headers", []):
        if key == b"cookie":
            jar = http_cookies.SimpleCookie()
            jar.load(value.decode("latin-1"))
            morsel = jar.get(name)
            return morsel.value if morsel else None
    return None


class WebSocketAuthMiddleware:
    """Refuse a websocket upgrade on a non-public path without a live session."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "websocket" or is_public(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        token = _cookie(scope, COOKIE_NAME)
        session = None
        if token:
            async with session_scope(scope["app"].state.sessionmaker) as db:
                session = await resolve_session(db, token)

        if session is None:
            # Closing before `websocket.accept` turns into a 403 on the handshake.
            await send({"type": "websocket.close", "code": WS_POLICY_VIOLATION})
            return

        # For the route: who this socket belongs to, and the token its watchdog
        # should watch. Same shape the HTTP gate leaves on request.state.
        scope.setdefault("state", {})
        scope["state"]["user_id"] = session.user_id
        scope["state"]["session_token"] = token

        await self.app(scope, receive, send)
