"""The apps endpoint — the catalogue the screen reads, against the live registry.

What matters here is the difference between three states a lazy endpoint would
flatten into one: in the table and running, in the table and *not* running, and
refused at start with a reason. The screen draws all three differently, so the
endpoint has to tell them apart.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.extensions.registry import Failed
from api.main import create_app
from api.models import Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """An admin and a viewer in one workspace, against a running application.

    The application's own lifespan loads the builtin extensions and syncs the
    catalogue, so the rows under test are the real ones, not hand-inserted copies.
    """
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()

    people = {}
    for username, role in (("mohamed", "admin"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        people[username] = user
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
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
