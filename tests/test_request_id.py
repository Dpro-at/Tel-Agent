"""Every request gets an id, the log line carries it, and concurrent requests do not share one.

The last of those is the acceptance condition for B4 and the reason a `ContextVar` was
used rather than a module-level variable. A global would pass every other test in this
file and fail only under concurrency — which is to say, only in production.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api import dependencies
from api.config import Settings
from api.logging import request_id_var
from api.main import create_app
from api.middleware.request_id import HEADER


async def test_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")

    # Parses as a UUID, or it is not an id.
    assert uuid.UUID(response.headers[HEADER])


async def test_each_request_gets_a_different_id(client: AsyncClient) -> None:
    first = await client.get("/health")
    second = await client.get("/health")

    assert first.headers[HEADER] != second.headers[HEADER]


async def test_a_well_formed_incoming_id_is_reused(client: AsyncClient) -> None:
    """A proxy or the dashboard may have assigned one already; carrying it through is
    how a single id spans more than one service."""
    given = str(uuid.uuid4())

    response = await client.get("/health", headers={HEADER: given})

    assert response.headers[HEADER] == given


@pytest.mark.parametrize(
    "forged",
    [
        "not-a-uuid",
        "../../etc/passwd",
        'injected" \n{"level":"INFO","message":"all clear"}',
    ],
)
async def test_a_malformed_incoming_id_is_replaced(client: AsyncClient, forged: str) -> None:
    """A caller must not be able to write arbitrary text into our logs.

    A newline in an accepted id turns one log line into two, and the second one can
    claim whatever it likes.
    """
    response = await client.get("/health", headers={HEADER: forged})

    assert response.headers[HEADER] != forged
    assert uuid.UUID(response.headers[HEADER])


async def test_concurrent_requests_do_not_share_an_id(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests in flight at once see two different ids, inside the handler.

    The handler blocks until both have arrived, so the second request's id is assigned
    while the first is still being served. A shared global would report one id twice.
    """
    monkeypatch.setattr(
        dependencies, "PUBLIC_PATHS", dependencies.PUBLIC_PATHS | {"/concurrency-probe"}
    )
    app = create_app(settings)
    both_arrived = asyncio.Event()
    seen: list[str | None] = []
    arrivals = 0

    @app.get("/concurrency-probe")
    async def probe() -> dict[str, str]:
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both_arrived.set()
        await both_arrived.wait()
        # Read from the context, not from the request: this is what a helper three
        # layers down would see.
        seen.append(request_id_var.get())
        return {"ok": "true"}

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            responses = await asyncio.gather(
                client.get("/concurrency-probe"),
                client.get("/concurrency-probe"),
            )

    assert len(seen) == 2
    assert None not in seen
    assert seen[0] != seen[1]
    assert {r.headers[HEADER] for r in responses} == set(seen)


async def test_the_access_line_is_json_and_carries_the_id(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The access line is written after the context variable is reset, so it passes the
    id explicitly. This asserts the filter does not overwrite it with null."""
    from api.logging import JsonFormatter, RequestIdFilter

    with caplog.at_level(logging.INFO, logger="api.access"):
        response = await client.get("/health")

    record = next(r for r in caplog.records if r.name == "api.access")
    RequestIdFilter().filter(record)
    line = json.loads(JsonFormatter().format(record))

    assert line["request_id"] == response.headers[HEADER]
    assert line["method"] == "GET"
    assert line["path"] == "/health"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], float)


def test_the_formatter_emits_one_line_per_record() -> None:
    """A traceback must not turn one record into several lines of output."""
    from api.logging import JsonFormatter

    try:
        raise ValueError("something with\nnewlines in it")
    except ValueError:
        # `exc_info=True` is resolved by `Logger._log`, never by `LogRecord` itself.
        # A hand-built record needs the real tuple.
        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="api.test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="failed",
            args=(),
            exc_info=exc_info,
        )

    output = JsonFormatter().format(record)

    assert "\n" not in output
    assert json.loads(output)["level"] == "ERROR"


def test_configure_logging_replaces_only_its_own_handler() -> None:
    """Called once per application built, it must not stack - or clobber.

    Two failures are possible here and both have been real. Stacking prints every line
    twice, which makes the JSON useless to anything parsing it. Clobbering removes
    handlers this module did not install, which silently disables whatever attached
    one first - an error reporter in production, `caplog` in this suite.
    """
    from api.logging import _HANDLER_NAME, configure_logging

    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        configure_logging("INFO")
        configure_logging("INFO")

        ours = [h for h in root.handlers if h.get_name() == _HANDLER_NAME]
        assert len(ours) == 1
        assert foreign in root.handlers
    finally:
        root.removeHandler(foreign)


def test_the_app_builds_without_leaving_the_probe_route(settings: Settings) -> None:
    """A guard on the fixture above: the probe is added to its own app, not the shared one."""
    app: FastAPI = create_app(settings)

    assert not any(
        route.path == "/concurrency-probe" for route in app.routes if hasattr(route, "path")
    )
