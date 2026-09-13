"""The apps endpoint — the catalogue the screen reads, against the live registry.

What matters here is the difference between three states a lazy endpoint would
flatten into one: in the table and running, in the table and *not* running, and
refused at start with a reason. The screen draws all three differently, so the
endpoint has to tell them apart.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.extensions import installs
from api.extensions.registry import Failed
from api.main import create_app
from api.models import App, AppInstall, AuthEvent, Channel, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
BACKFILL = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "2026_09_13_0900-6b2e9d4f1a73_backfill_app_installs.py"
)


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """An admin and a viewer in one workspace, against a running application.

    The application's own lifespan loads the builtin extensions and syncs the
    catalogue, so the rows under test are the real ones, not hand-inserted copies.
    """
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    other = Workspace(name="Wolf Studio")
    migrated.add(other)
    await migrated.flush()

    people = {}
    for username, role in (("mohamed", "admin"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        people[username] = user
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    # Read before the commit expires them. The second workspace belongs to nobody
    # here; it is what proves a switch in one workspace stays in that workspace.
    ids = {"workspace": workspace.id, "other": other.id}
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    app.state.ids = ids
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in people:
            client = AsyncClient(transport=transport, base_url="http://localhost")
            response = await client.post(
                "/api/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert response.status_code == 200
            clients[username] = client
        try:
            yield app, clients
        finally:
            for client in clients.values():
                await client.aclose()


async def test_the_three_official_applications_are_listed_and_running(stage) -> None:
    """The core registers itself through the same contract as anything else —
    D-031's words — so the endpoint lists it the same way."""
    _app, clients = stage

    body = (await clients["mohamed"].get("/api/apps")).json()

    by_slug = {entry["slug"]: entry for entry in body["installed"]}
    assert set(by_slug) >= {"agent_core", "database", "web_chat"}
    for entry in by_slug.values():
        assert entry["running"] is True
        assert entry["origin"] == "official"

    chat = by_slug["web_chat"]
    assert chat["name"] == "Web chat"
    assert chat["category"] == "channels"
    assert chat["version"] == "0.1.0"
    # The reviewable claim travels to the screen.
    assert "messages.write" in chat["scopes"]
    assert chat["hooks"] == ["message.received"]


async def test_a_row_that_is_not_live_says_so(stage) -> None:
    """In the table and not in this process is a real state — an extension that
    loaded once and was refused on the last start. It must not be drawn as running."""
    app, clients = stage
    del app.state.extensions.loaded["web_chat"]

    body = (await clients["mohamed"].get("/api/apps")).json()

    by_slug = {entry["slug"]: entry for entry in body["installed"]}
    assert by_slug["web_chat"]["running"] is False
    assert by_slug["agent_core"]["running"] is True


async def test_a_refusal_travels_with_its_reason(stage) -> None:
    """The reason is the whole value of recording a refusal: "it did not load" sends
    an operator searching, the reason sends them to the line."""
    app, clients = stage
    app.state.extensions.failed.append(
        Failed(slug="telegram_bridge", reason="import failed: ModuleNotFoundError(...)")
    )

    body = (await clients["mohamed"].get("/api/apps")).json()

    assert body["refused"] == [
        {"slug": "telegram_bridge", "reason": "import failed: ModuleNotFoundError(...)"}
    ]


async def test_a_viewer_is_refused(stage) -> None:
    """Operations data, gated like the backup overview: admin and above."""
    _app, clients = stage

    response = await clients["lukas"].get("/api/apps")

    assert response.status_code == 403
    assert "admin" in response.json()["error"]["message"]


async def test_signed_out_sees_nothing(stage) -> None:
    _app, clients = stage
    clients["mohamed"].cookies.clear()

    assert (await clients["mohamed"].get("/api/apps")).status_code == 401


# --- Per-workspace installation (#241) ----------------------------------------


async def _apps(client: AsyncClient) -> dict[str, dict]:
    body = (await client.get("/api/apps")).json()
    return {entry["slug"]: entry for entry in body["installed"]}


async def test_each_app_says_whether_this_workspace_has_it(stage) -> None:
    """Three states the screen draws differently: part of the core, installed here,
    and known to the installation but not installed here."""
    _app, clients = stage

    by_slug = await _apps(clients["mohamed"])

    core = by_slug["agent_core"]
    assert (core["system"], core["installed"], core["enabled"]) == (True, True, True)
    telegram = by_slug["telegram"]
    assert (telegram["system"], telegram["installed"], telegram["enabled"]) == (
        False,
        False,
        False,
    )


async def test_switching_an_app_on_installs_it(stage, migrated: AsyncSession) -> None:
    app, clients = stage

    answer = await clients["mohamed"].put("/api/apps/telegram", json={"enabled": True})

    assert answer.status_code == 200, answer.text
    assert (answer.json()["installed"], answer.json()["enabled"]) == (True, True)
    assert (await _apps(clients["mohamed"]))["telegram"]["enabled"] is True
    assert await installs.is_enabled(migrated, app.state.ids["workspace"], "telegram")
    # The workspace next door did not get it.
    assert not await installs.is_enabled(migrated, app.state.ids["other"], "telegram")

    event = await migrated.scalar(select(AuthEvent).where(AuthEvent.event == "app_changed"))
    assert event is not None
    assert event.details["slug"] == "telegram"
    assert event.details["enabled"] is True


async def test_switching_a_channel_app_off_switches_its_channels_off(
    stage, migrated: AsyncSession
) -> None:
    """Off under Apps means off - including a channel that was answering customers a
    moment ago, and only in this workspace."""
    app, clients = stage
    mine, theirs = app.state.ids["workspace"], app.state.ids["other"]
    for workspace_id in (mine, theirs):
        await installs.install(migrated, workspace_id, "telegram")
        migrated.add(
            Channel(
                workspace_id=workspace_id, kind="telegram", name="Telegram", status="active"
            )
        )
    await migrated.commit()

    answer = await clients["mohamed"].put("/api/apps/telegram", json={"enabled": False})

    assert answer.status_code == 200, answer.text
    assert (answer.json()["installed"], answer.json()["enabled"]) == (True, False)
    migrated.expire_all()
    statuses = dict(
        (
            await migrated.execute(
                select(Channel.workspace_id, Channel.status).where(Channel.kind == "telegram")
            )
        )
        .tuples()
        .all()
    )
    assert statuses == {mine: "disabled", theirs: "active"}


async def test_a_channel_card_cannot_switch_on_what_apps_switched_off(stage) -> None:
    _app, clients = stage
    web_on = {"enabled": True, "allowed_origins": ["https://shop.test"]}

    # Not installed: refused, with the code the card branches on.
    refused = await clients["mohamed"].put("/api/channels/web", json=web_on)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "app_disabled"

    assert (
        await clients["mohamed"].put("/api/apps/web_chat", json={"enabled": True})
    ).status_code == 200
    accepted = await clients["mohamed"].put("/api/channels/web", json=web_on)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["enabled"] is True

    # Switched off one level up: the channel went off with it, and stays off.
    assert (
        await clients["mohamed"].put("/api/apps/web_chat", json={"enabled": False})
    ).status_code == 200
    assert (await clients["mohamed"].get("/api/channels/web")).json()["enabled"] is False
    again = await clients["mohamed"].put("/api/channels/web", json={"enabled": True})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "app_disabled"

    # Switching a channel off is never refused.
    assert (
        await clients["mohamed"].put("/api/channels/web", json={"enabled": False})
    ).status_code == 200


async def test_an_unknown_or_core_app_cannot_be_switched(stage) -> None:
    _app, clients = stage

    unknown = await clients["mohamed"].put("/api/apps/fax_machine", json={"enabled": True})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "app_not_found"

    for slug in installs.SYSTEM_APPS:
        core = await clients["mohamed"].put(f"/api/apps/{slug}", json={"enabled": False})
        assert core.status_code == 409
        assert core.json()["error"]["code"] == "app_is_system"


async def test_a_viewer_cannot_switch_an_app(stage) -> None:
    _app, clients = stage

    answer = await clients["lukas"].put("/api/apps/telegram", json={"enabled": True})

    assert answer.status_code == 403


async def test_a_new_workspace_starts_with_web_chat(stage) -> None:
    _app, clients = stage

    created = await clients["mohamed"].post("/api/workspaces", json={"name": "Second shop"})
    assert created.status_code in (200, 201), created.text
    clients["mohamed"].headers["X-Workspace-Id"] = str(created.json()["id"])

    by_slug = await _apps(clients["mohamed"])
    assert by_slug["web_chat"]["installed"] is True
    assert by_slug["web_chat"]["enabled"] is True
    assert by_slug["telegram"]["installed"] is False


# --- The backfill revision ----------------------------------------------------


@pytest.fixture
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Only so a fixture row can hold a credential, the way a set-up channel does."""
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", "aa" * 32)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


def _revision() -> ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_app_installs", BACKFILL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_backfill_installs_what_each_workspace_was_already_using(
    migrated: AsyncSession, encryption_key: None
) -> None:
    """Without it, every existing workspace would find every channel switch refused -
    web chat included - the day this version is installed."""
    first = Workspace(name="Wagner & Partner")
    second = Workspace(name="Wolf Studio")
    migrated.add_all([first, second])
    await migrated.flush()
    first_id, second_id = first.id, second.id
    migrated.add_all(
        [
            Channel(workspace_id=first_id, kind="web", name="Web chat"),
            # Switched on: installed.
            Channel(workspace_id=first_id, kind="telegram", name="Telegram", status="active"),
            # Off, but set up with a credential: installed.
            Channel(
                workspace_id=first_id,
                kind="slack",
                name="Slack",
                status="disabled",
                credentials_encrypted=json.dumps({"bot_token": "x"}),
            ),
            # A card somebody opened once and never filled in: not installed.
            Channel(workspace_id=first_id, kind="discord", name="Discord", status="disabled"),
        ]
    )
    await migrated.flush()
    # A workspace that already has web chat installed and switched off keeps it off.
    await installs.install(migrated, second_id, "web_chat", enabled=False)
    await migrated.commit()

    revision = _revision()
    await migrated.run_sync(lambda session: revision.backfill(session.connection()))
    # Running it twice changes nothing.
    await migrated.run_sync(lambda session: revision.backfill(session.connection()))
    await migrated.commit()

    migrated.expire_all()
    rows = (
        await migrated.execute(
            select(AppInstall.workspace_id, App.slug, AppInstall.enabled).join(
                App, App.id == AppInstall.app_id
            )
        )
    ).tuples()
    assert sorted(rows) == [
        (first_id, "slack", True),
        (first_id, "telegram", True),
        (first_id, "web_chat", True),
        (second_id, "web_chat", False),
    ]
    # The apps it had to add carry an empty manifest until the next catalogue sync.
    slack = await migrated.scalar(select(App).where(App.slug == "slack"))
    assert slack is not None and slack.origin == "official"
