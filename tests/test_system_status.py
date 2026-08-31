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


async def test_the_chat_says_not_configured_until_somebody_can_reach_it(
    migrated: AsyncSession, settings: Settings
):
    """A row exists for the web channel long before a bubble does.

    A channel with no address is one nobody can embed and a disabled one refuses every
    message, so neither is "configured" - the question an owner is asking is whether
    the bubble on their site works, not whether a row exists.
    """
    from api.models import Channel, Workspace

    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()

    # Configured but switched off, and live but with no address: neither counts.
    migrated.add_all(
        [
            Channel(
                workspace_id=workspace.id,
                kind="web",
                name="Old",
                webhook_path="off-" + "a" * 20,
                status="disabled",
            ),
            Channel(workspace_id=workspace.id, kind="web", name="New", status="active"),
        ]
    )
    await migrated.commit()

    states = {
        service["id"]: service["state"]
        for service in (await status.collect(migrated, settings))["services"]
    }
    assert states["web_channel"] == "not_configured"

    migrated.add(
        Channel(
            workspace_id=workspace.id,
            kind="web",
            name="Live",
            webhook_path="live-" + "b" * 20,
            status="active",
        )
    )
    await migrated.commit()

    report = await status.collect(migrated, settings)
    live = next(s for s in report["services"] if s["id"] == "web_channel")
    assert live["state"] == "ok"
    assert live["detail"] == "1 live"


async def test_the_model_row_answers_without_calling_the_model(
    migrated: AsyncSession, settings: Settings, monkeypatch
):
    """Three states, and none of them costs a request.

    A health screen that spends a model call every time it is opened is a health screen
    with a bill. What an owner needs from this row - whether this installation has a
    model at all - is answerable from the configuration.

    The environment is still one of the two sources §B9.2 leaves in place, so it is what
    this drives; `test_llm_settings.py` owns the store and the order between them.
    """
    from agent.config import llm_settings

    def row(report):
        return next(s for s in report["services"] if s["id"] == "llm")

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    llm_settings.cache_clear()
    assert row(await status.collect(migrated, settings))["state"] == "not_configured"

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "a-model")
    monkeypatch.setenv("LLM_API_KEY", "a-secret-key")
    llm_settings.cache_clear()
    connected = row(await status.collect(migrated, settings))
    assert connected["state"] == "ok"
    assert "a-model" in connected["detail"]
    # The key is never handed back, and this endpoint being admin-only is not a reason
    # to start.
    assert "a-secret-key" not in connected["detail"]

    # Half a configuration is worse than none: the agent refuses to answer, and without
    # this row the owner has no way to see why from outside a conversation.
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm_settings.cache_clear()
    broken = row(await status.collect(migrated, settings))
    assert broken["state"] == "down"
    # Named as the *screen* names it, not as `.env` does, whichever source the half
    # configuration came from: the remedy is the same either way, because a value
    # typed into the settings screen wins over a stale environment variable.
    assert "the API key" in broken["detail"]
    llm_settings.cache_clear()


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
