"""One error envelope for the whole API.

The dashboard has to render failures. Handlers that each invent their own error body
turn that into guesswork per screen — and twenty-nine screens is twenty-nine guesses.
So every failure, from a rejected field to an unhandled exception, comes back in the
same shape.

An unhandled exception logs its traceback and returns the request id. It never returns
the traceback: a stack trace names internal paths, library versions and sometimes the
values that caused the failure, and the API is reachable by anyone who can reach the
dashboard.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.logging import request_id_var

logger = logging.getLogger("api.errors")


class ErrorBody(BaseModel):
    """The contents of a failure."""

    code: str = Field(
        description="A stable, machine-readable identifier. Screens branch on this, "
        "never on the message, which is prose and will be translated.",
        examples=["not_found"],
    )
    message: str = Field(
        description="What went wrong, in one sentence.",
        examples=["The requested resource does not exist."],
    )
    details: list[dict[str, Any]] | None = Field(
        default=None,
        description="Per-field information when the failure is a validation error. "
        "Null for everything else.",
    )
    request_id: str | None = Field(
        default=None,
        description="The id of the request that failed, matching the X-Request-Id "
        "response header and the line in the server log.",
    )


class ErrorResponse(BaseModel):
    """The envelope. Every non-2xx response from this API has this shape."""

    error: ErrorBody


def envelope_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    """Build the envelope directly.

    Exported because middleware has to produce one too: a request refused before any
    route runs must look exactly like a request refused inside one, or the dashboard
    has two failure shapes to handle instead of the one it was promised.
    """
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message=message,
            details=details,
            request_id=request_id_var.get(),
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


# HTTP status codes mapped to the stable codes screens branch on. Anything not listed
# falls back to `http_error`, which is honest: an unmapped status has no meaning the
# frontend could act on beyond the number itself.
_CODES = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthenticated",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
}


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """`HTTPException` raised by a route, and Starlette's own 404 and 405."""
    response = envelope_response(
        status_code=exc.status_code,
        code=_CODES.get(exc.status_code, "http_error"),
        message=str(exc.detail),
    )
    # Preserve `WWW-Authenticate` and anything else the raiser attached; dropping it
    # would break the authentication challenge that arrives with the login routes.
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """A rejected request body, query string or path parameter.

    The per-field detail is kept, because that is what lets a form highlight the field
    that was wrong instead of showing one message above the whole form.
    """
    details = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return envelope_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="validation_error",
        message="The request could not be processed as submitted.",
        details=details,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything that reached the top of the stack unhandled.

    The traceback goes to the log, correlated by request id. The client gets the id and
    nothing else — an operator can find the line, and an attacker gets no map of the
    internals.
    """
    logger.exception(
        "unhandled exception",
        extra={"method": request.method, "path": request.url.path},
    )
    return envelope_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="Something went wrong on the server. The failure has been logged.",
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler on the application."""
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
