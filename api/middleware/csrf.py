"""Cross-site request forgery protection, by origin check.

The moment authentication became a cookie, every state-changing endpoint became
reachable from any other site the user has open: their browser attaches the session
cookie to a form post or a fetch no matter which page started it. `SameSite=Lax` on
the cookie already stops the plain `<form>` case; this middleware closes the rest.

**The rule.** An unsafe request (POST, PUT, PATCH, DELETE) that carries an `Origin`
header must carry an allowed one: the dashboard's configured origins, or the
installation's own. A request with **no** `Origin` header passes.

That last clause is deliberate and worth being precise about. CSRF is a browser
attack — it exists because the browser attaches cookies to requests other sites
trigger. Every current browser sends `Origin` on exactly those requests (cross-site
unsafe fetches, form posts, WebSocket handshakes), so the attack always arrives with
the header, and refusing on a mismatch defeats it. What arrives *without* the header
is curl, server-to-server calls, and monitoring scripts — clients that hold their
credentials themselves and cannot be steered by a malicious page. Refusing those would
break §B6's public API without stopping anything.

An origin-check rather than a double-submit token, on purpose: it needs no state, no
token plumbing through twenty-nine screens, and it cannot fall out of sync. Its one
prerequisite is already met — `TrustedHostMiddleware` validates the `Host` header this
middleware compares against, so a forged Host cannot be used to launder an origin.

**Written as pure ASGI, not `BaseHTTPMiddleware`.** The task's hardest sentence is
"applies to WebSocket upgrades too — they carry cookies and are not covered by CORS".
`BaseHTTPMiddleware` never sees a websocket scope at all, so a CSRF layer built on it
would silently cover everything except the one path the spec singles out. This one
sees both.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from api.errors import envelope_response

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("api.csrf")

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The one family of routes this check does not apply to, and why it is safe.
#
# Read the rule above again: CSRF exists because the browser attaches the session
# cookie to a request another site triggered. `/public/` carries no cookie and opens no
# session - there is nothing for a malicious page to make the browser attach, so there
# is nothing to forge. The check would not protect it; it would only make it
# unreachable, because the origin it must accept is the *customer's own site*, which
# can never appear in this installation's `cors_origins`.
#
# What guards it instead is stricter than this: a per-channel allowlist, where an
# unconfigured channel refuses everything (§B14, `api/security/embed.py`).
#
# **The exemption holds only while these routes stay session-less.** A `/public/` route
# that ever reads a session would be exempt from CSRF *and* authenticated, which is the
# one combination this file exists to prevent. `tests/test_csrf.py` asserts it.
CSRF_EXEMPT_PREFIXES: tuple[str, ...] = ("/public/",)


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _host_of(origin: str) -> str | None:
    """`host[:port]` of an Origin header value, or None if it does not parse.

    `Origin: null` — sandboxed iframes, some redirects — parses to no host and is
    therefore refused, which is the safe reading: nothing legitimate in this product
    runs sandboxed.
    """
    try:
        parts = urlsplit(origin)
    except ValueError:
        return None
    return parts.netloc.lower() or None


def origin_allowed(
    origin: str, *, request_host: str | None, allowed_origins: list[str]
) -> bool:
    """Is this origin one of ours?

    Two ways in:

    * an exact match against the configured dashboard origins (`CORS_ORIGINS`), the
      same list the browser is told it may call us from; or
    * the same host the request itself was addressed to — the dashboard served by the
      installation, whatever scheme sits in front of it.

    The same-host comparison ignores the scheme on purpose. Behind a reverse proxy the
    browser says `https://telagent.local` while the request reaches this process as
    plain HTTP, and comparing schemes would refuse every installation that terminates
    TLS in front — the recommended deployment. A scheme downgrade is a
    man-in-the-middle problem, which no CSRF check defends against anyway.
    """
    if origin in allowed_origins:
        return True

    origin_host = _host_of(origin)
    if origin_host is None:
        return False
    return request_host is not None and origin_host == request_host.lower()


class CsrfMiddleware:
    """Refuse unsafe requests and websocket handshakes from foreign origins."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Unsafe HTTP methods, and every websocket handshake. A websocket has no
        # method to classify, and its handshake both carries cookies and escapes
        # CORS entirely - it is checked unconditionally.
        if scope["type"] == "http" and scope.get("method") not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if path.startswith(CSRF_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        origin = _header(scope, b"origin")
        if origin is None:
            # No Origin means no browser page steered this request - see the module
            # docstring for why that passes rather than being refused.
            await self.app(scope, receive, send)
            return

        settings = scope["app"].state.settings
        if origin_allowed(
            origin,
            request_host=_header(scope, b"host"),
            allowed_origins=settings.cors_origins,
        ):
            await self.app(scope, receive, send)
            return

        logger.warning(
            "cross-origin request refused",
            extra={"origin": origin, "path": scope.get("path")},
        )

        if scope["type"] == "websocket":
            # The handshake never completes. 403 during the HTTP upgrade would be
            # kinder, but plain ASGI only offers close here without hand-writing the
            # response - and a refused handshake is refused either way.
            await send({"type": "websocket.close", "code": 1008})
            return

        response = envelope_response(
            status_code=403,
            code="forbidden",
            message="This request came from a site this installation does not serve.",
        )
        await response(scope, receive, send)
