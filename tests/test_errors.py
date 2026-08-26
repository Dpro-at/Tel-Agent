"""Every failure comes back in one envelope, and an unhandled one leaks nothing.

B5's acceptance condition is that a deliberate 500 returns the envelope with a request
id and no internals. That is asserted here against a route that raises on purpose.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from api import dependencies
from api.config import Settings
from api.main import create_app
from api.middleware.request_id import HEADER

# A fake credential, on purpose. The whole point of the 500 test below is that a
# connection string appearing in an exception message never reaches the client, and
# there is no way to assert that without a realistic-looking string to look for.
# The rule stays on for the rest of the suite; only this line is excused.
SECRET = "postgresql://admin:hunter2@db.internal/telagent"  # noqa: S105


class Payload(BaseModel):
    name: str
    count: int


@pytest.fixture
async def failing_client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """An application with routes that fail in each of the ways the handlers cover.

    Opened to unauthenticated callers for the duration: routes are closed by default
    (D5), and this file is about the shape of a failure, not about who may see one.
    """
    monkeypatch.setattr(
        dependencies,
        "PUBLIC_PATHS",
        dependencies.PUBLIC_PATHS | {"/boom", "/teapot", "/validated", "/nope"},
    )
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        # Carries a credential in the message on purpose: the test asserts it does not
        # reach the client.
        raise RuntimeError(f"connection refused to {SECRET}")

    @app.get("/teapot")
    async def teapot() -> None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already exists.")

    @app.post("/validated")
    async def validated(payload: Payload) -> Payload:
        return payload

    # The authentication middleware opens the request's database session, so it needs
    # the sessionmaker that `lifespan` creates. Without this every request here is a
    # 500 from a missing engine rather than the failure the test is about.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client


async def test_not_found_uses_the_envelope(failing_client: AsyncClient) -> None:
    response = await failing_client.get("/nope")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["details"] is None
    assert error["request_id"] == response.headers[HEADER]


async def test_a_raised_http_exception_is_mapped(failing_client: AsyncClient) -> None:
    response = await failing_client.get("/teapot")

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "conflict",
        "message": "Already exists.",
        "details": None,
        "request_id": response.headers[HEADER],
    }


async def test_validation_errors_name_the_field(failing_client: AsyncClient) -> None:
    """A form has to highlight the field that was wrong, not print one message above it."""
    response = await failing_client.post("/validated", json={"count": "not a number"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"

    fields = {detail["field"] for detail in error["details"]}
    assert fields == {"name", "count"}


async def test_an_unhandled_exception_returns_the_envelope_and_no_internals(
    failing_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        response = await failing_client.get("/boom")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] == response.headers[HEADER]

    # The whole point: the traceback and the credential in it stay on the server.
    body = response.text
    assert SECRET not in body
    assert "Traceback" not in body
    assert "RuntimeError" not in body
    assert "api/main.py" not in body

    # And it really was logged, with the traceback, under the same id.
    logged = "\n".join(
        record.getMessage() + (record.exc_text or "") for record in caplog.records
    )
    assert "unhandled exception" in logged


async def test_the_error_shape_is_the_same_across_every_failure(
    failing_client: AsyncClient,
) -> None:
    """One shape, so twenty-nine screens do not each guess at a different one."""
    responses = [
        await failing_client.get("/nope"),
        await failing_client.get("/teapot"),
        await failing_client.get("/boom"),
        await failing_client.post("/validated", json={}),
    ]

    for response in responses:
        error = response.json()["error"]
        assert set(error) == {"code", "message", "details", "request_id"}
        assert isinstance(error["code"], str)
        assert isinstance(error["message"], str)
        assert error["request_id"]
