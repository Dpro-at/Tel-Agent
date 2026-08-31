"""Ceilings on what one request may cost — G2.

**The threat is a mistake, not an attacker.** An unbounded request body and an
unbounded handler are both a denial of service that nobody has to intend: a loop that
posts a growing payload, a query that turns out to be quadratic on a database somebody
let grow. The point of a ceiling is that the failure is one refused request instead of
a process that stops answering.

Two limits live here, and one deliberately does not.

**Body size** is checked twice. `Content-Length` refuses the honest oversized request
before a single byte is read, and the body is then read here, up to the ceiling and no
further — a chunked request declares no length at all, so a header check on its own is
a limit any client can opt out of by not mentioning it.

Reading it here rather than counting as the handler reads is not a preference. An
exception raised on the receive channel is swallowed on the way out: the framework is
parsing a body at that moment, so what reaches the client is "could not parse the
body", a 400 that blames the caller's JSON for a limit they exceeded. Buffering is
bounded by the ceiling itself — that is what the ceiling is — and it makes the refusal
the one the caller can act on.

**Time is measured to the first byte of the response, not to the last.** This is the
subtle one, and getting it wrong would break the product's central rule: an agent
streams its answer, so a wall clock over the whole response would cut off exactly the
long replies Rule 3 exists to make possible. The deadline is therefore cancelled the
moment `http.response.start` goes out, which covers routing, authorisation and the
handler's own work, and leaves a stream to run for as long as somebody is reading it.

**Page size is not enforced here.** A cap belongs on the endpoint that knows what a
page of its own rows costs, declared as `Query(le=...)` so it is in the OpenAPI
document and refused by validation with the same envelope. A middleware guessing a
number for every list at once would be a number that is wrong for all of them.
"""

from __future__ import annotations

import asyncio
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.errors import envelope_response

logger = logging.getLogger("api.limits")


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                # A malformed length is not a length. `_read` measures the body
                # either way, so this falls through to being counted rather than
                # trusted.
                return None
    return None


class RequestLimitsMiddleware:
    """Refuse a request that is too large, or one that takes too long to answer."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int, timeout_seconds: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Websockets are exempt from both. A handshake carries no body, and a
        # connection that is open for an hour is what a websocket is for - a deadline
        # here would close every live transcript at thirty seconds.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_body_bytes:
            # Refused without reading a byte. Reading the body first to find out how
            # big it is would be paying the exact cost the limit exists to avoid.
            await self._refuse(scope, send, oversized=True, path=scope.get("path"))
            return

        body, oversized = await self._read(receive)
        if oversized:
            await self._refuse(scope, send, oversized=True, path=scope.get("path"))
            return

        # The body, replayed once. Everything after it is the disconnect channel, which
        # the application still needs in order to notice a visitor who closed the tab -
        # that is how cancellation reaches a stream.
        replayed = False

        async def replaying_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        started = False

        try:
            async with asyncio.timeout(self.timeout_seconds) as deadline:

                async def watching_send(message: Message) -> None:
                    nonlocal started
                    if message["type"] == "http.response.start":
                        started = True
                        # The answer has begun, so the handler met its deadline. What
                        # follows is a body being read at the reader's pace, and it is
                        # not this middleware's business how long that takes.
                        deadline.reschedule(None)
                    await send(message)

                await self.app(scope, replaying_receive, watching_send)
        except TimeoutError:
            # Only reachable before the response started - the deadline is gone by
            # then. The handler's task has been cancelled, so its `finally` has run and
            # nothing is left half-written.
            if not started:
                await self._refuse(scope, send, oversized=False, path=scope.get("path"))

    async def _read(self, receive: Receive) -> tuple[bytes, bool]:
        """The body, or the moment it passed the ceiling.

        Stops at the first byte over, rather than draining the rest to find out how
        much over it was: the caller has already been told the answer is no.
        """
        chunks: list[bytes] = []
        seen = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # A disconnect before the body finished. Nothing to refuse and nothing
                # to hand on; the empty body lets the application see the disconnect.
                return b"", False
            chunks.append(message.get("body", b""))
            seen += len(chunks[-1])
            if seen > self.max_body_bytes:
                return b"", True
            if not message.get("more_body"):
                return b"".join(chunks), False

    async def _refuse(
        self, scope: Scope, send: Send, *, oversized: bool, path: str | None
    ) -> None:
        if oversized:
            logger.warning("request body over the limit", extra={"path": path})
            response = envelope_response(
                status_code=413,
                code="body_too_large",
                message=f"That request body is larger than this installation accepts "
                f"({self.max_body_bytes} bytes).",
            )
        else:
            logger.warning("request timed out before answering", extra={"path": path})
            response = envelope_response(
                status_code=504,
                code="request_timeout",
                message=f"This request took longer than {self.timeout_seconds} seconds "
                "to answer and was stopped.",
            )
        # Sent through the plain channel: the body was never consumed on the refused
        # path, and the response does not read one.
        await response(scope, _no_body, send)


async def _no_body() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
