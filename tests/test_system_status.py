"""The detail behind the health screen — the wiring phase.

The test that matters is the one about honesty: a service nothing has been built for
must not report as healthy, and it must not report as broken either.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Membership, User, Workspace
from api.security.password import hash_password
from api.settings import store
from api.system import status

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def clients(migrated: AsyncSession, settings: Settings, database_url: str):
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()
    for username, role in (("mohamed", "admin"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    opened: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "lukas"):
            http = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            opened[username] = http
        try:
            yield opened
        finally:
            for http in opened.values():
                await http.aclose()


async def test_a_service_nobody_has_built_is_not_green(
    migrated: AsyncSession, settings: Settings
):
    """The whole point of this endpoint.

    Reporting SIP as healthy would be a lie that survives until the first real call.
    Reporting it as down would be a different lie, and would teach the owner to ignore
    a red dot before the dot ever means anything.
    """
    report = await status.collect(migrated, settings)

    states = {service["id"]: service["state"] for service in report["services"]}
    for unbuilt in status.UNBUILT:
        assert states[unbuilt] == "not_configured", unbuilt


async def test_an_unconfigured_installation_is_not_degraded(
    migrated: AsyncSession, settings: Settings
):
    """ "Degraded" has to keep meaning something for the day it is true."""
    report = await status.collect(migrated, settings)

    assert report["verdict"] == "ok"


async def test_the_database_is_timed_not_merely_pinged(
    migrated: AsyncSession, settings: Settings
):
    """A database answering in 900 ms is not healthy, and "up" is the wrong word."""
    report = await status.collect(migrated, settings)

    database = next(s for s in report["services"] if s["id"] == "db")
    assert database["state"] == "ok"
    assert isinstance(database["latency_ms"], float)


async def test_mail_is_not_configured_until_a_host_is_set(
    migrated: AsyncSession, settings: Settings
):
    report = await status.collect(migrated, settings)

    smtp = next(s for s in report["services"] if s["id"] == "smtp")
    assert smtp["state"] == "not_configured"


async def test_a_mail_host_that_does_not_answer_is_down(
    migrated: AsyncSession, settings: Settings
):
    """The screen's stale-SMTP state, reached the way it is reached in real life."""
    await store.set_value(migrated, "smtp.host", "127.0.0.1")
    await store.set_value(migrated, "smtp.port", 1)
    await migrated.commit()

    report = await status.collect(migrated, settings)

    smtp = next(s for s in report["services"] if s["id"] == "smtp")
    assert smtp["state"] == "down"
    # The host and port, because that is the part an operator acts on.
    assert "127.0.0.1:1" in (smtp["detail"] or "")


async def test_a_viewer_cannot_read_the_detail(clients) -> None:
    """Same argument as the log: it names hosts, providers and paths."""
    assert (await clients["lukas"].get("/api/system/status")).status_code == 403


async def test_an_admin_can(clients) -> None:
    response = await clients["mohamed"].get("/api/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] in ("ok", "degraded", "down")
    assert body["storage"]["total_bytes"] is None or body["storage"]["total_bytes"] > 0


async def test_signed_out_is_refused(clients) -> None:
    clients["mohamed"].cookies.clear()

    assert (await clients["mohamed"].get("/api/system/status")).status_code == 401
