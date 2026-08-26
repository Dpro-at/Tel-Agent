"""One id per request, one access line per request.

The id is returned as `X-Request-Id` so that an operator looking at a failed screen can
read it off the response and find the exact line in the log. It is the same id the
error envelope carries (`api/errors.py`), which is what makes "it failed for me at
14:02" a query rather than a search.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from api.errors import handle_unexpected_error
from api.logging import request_id_var

HEADER = "X-Request-Id"

logger = logging.getLogger("api.access")


def _incoming_id(request: Request) -> str | None:
    """Reuse a caller's id only when it is a well-formed UUID.

    A reverse proxy or the dashboard may already have assigned one, and carrying it
    through is how a single id spans several services. Accepting *any* string would let
    a caller write whatever they like straight into our logs — a newline turns one log
    line into two, and the second one can say anything it wants.
    """
    candidate = request.headers.get(HEADER)
    if not candidate:
        return None
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return None


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign the id, log the request, and return the id on the response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _incoming_id(request) or str(uuid.uuid4())
        token = request_id_var.set(request_id)

        # `perf_counter` rather than wall clock: this is a duration, and a clock
        # adjustment mid-request must not be able to report a negative one.
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                # Turned into a response *here*, inside the id's scope, rather than
                # left to Starlette's ServerErrorMiddleware.
                #
                # That middleware sits outside this one. An exception allowed to pass
                # through would be rendered above us, after the context variable has
                # been reset and with no chance to set the header - so a 500 would
                # carry no id in the body and none in the header. The one failure an
                # operator most needs to correlate would be the only one they cannot.
                response = await handle_unexpected_error(request, exc)

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers[HEADER] = request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "request_id": request_id,
                },
            )
            return response
        finally:
            request_id_var.reset(token)
