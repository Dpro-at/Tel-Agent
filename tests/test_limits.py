"""Ceilings on one request — G2.

Built against a throwaway application rather than the real one, because what is being
tested is the middleware: making a real route slow enough to time out would mean
shipping a slow route. The last two tests are the ones that would actually hurt if the
middleware were wrong — a chunked body escaping the limit, and a stream being cut off
by a deadline meant for the handler.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient

from api.errors import install_error_handlers
from api.middleware.limits import RequestLimitsMiddleware

LIMIT = 2048


def _app(*, max_body_bytes: int = LIMIT, timeout_seconds: int = 30) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(
        RequestLimitsMiddleware,
        max_body_bytes=max_body_bytes,
        timeout_seconds=timeout_seconds,
    )

    @app.post("/echo")
    async def echo(payload: dict) -> dict:
        return {"seen": len(payload.get("text", ""))}

    @app.get("/slow")
    async def slow() -> dict:
        await asyncio.sleep(5)
        return {"never": True}

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            # The first chunk is what ends the deadline. Everything after it arrives
            # long past the timeout, which is the point.
            yield b"first"
            await asyncio.sleep(1.2)
            yield b"-last"

        return StreamingResponse(body(), media_type="text/plain")

    return app


@pytest.fixture
async def client():
    app = _app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://localhost") as http:
        yield http


async def test_a_body_within_the_limit_is_ordinary(client) -> None:
    answer = await client.post("/echo", json={"text": "x" * 100})
    assert answer.status_code == 200
    assert answer.json() == {"seen": 100}


async def test_a_declared_oversized_body_is_refused_in_the_standard_envelope(client) -> None:
    """Refused on `Content-Length`, before a byte is read. Reading it first to find out
    how big it is would be paying the exact cost the limit exists to avoid."""
    answer = await client.post("/echo", json={"text": "x" * (LIMIT * 2)})

    assert answer.status_code == 413
    body = answer.json()["error"]
    assert body["code"] == "body_too_large"
    # The same shape as every other refusal: the dashboard was promised one.
    assert set(body) == {"code", "message", "details", "request_id"}


async def test_a_chunked_body_cannot_opt_out_of_the_limit(client) -> None:
    """The test that justifies counting at all.

    A chunked request declares no `Content-Length`, so a header check on its own is a
    limit any client can skip by not mentioning it.
    """

    async def oversized() -> AsyncIterator[bytes]:
        for _ in range(8):
            yield b"x" * 512

    answer = await client.post(
        "/echo",
        content=oversized(),
        headers={"Content-Type": "application/json"},
    )

    assert "content-length" not in {name.lower() for name in answer.request.headers}
    assert answer.status_code == 413
    assert answer.json()["error"]["code"] == "body_too_large"


async def test_a_handler_that_never_answers_is_stopped(client) -> None:
    app = _app(timeout_seconds=1)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://localhost") as http:
        answer = await http.get("/slow")

    assert answer.status_code == 504
    assert answer.json()["error"]["code"] == "request_timeout"


async def test_a_stream_is_not_cut_off_by_the_handlers_deadline() -> None:
    """The one that protects Rule 3.

    The agent streams its answer, so a wall clock over the whole response would cut off
    exactly the long replies the product is built to make possible. The deadline covers
    the handler; it ends when the first byte does.
    """
    app = _app(timeout_seconds=1)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://localhost") as http:
        answer = await http.get("/stream")

    assert answer.status_code == 200
    # The second chunk arrives 1.2s in, well past the one-second deadline.
    assert answer.text == "first-last"
