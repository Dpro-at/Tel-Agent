"""Configuring the widget, which until now needed SQL.

The tests that matter are the ones about the two values that are not ordinary settings:
the allowlist, which is the guard from §B14 and must be validated by the same function
that enforces it, and the reCAPTCHA secret, which must never come back out.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    password_hash = hash_password(PASSWORD)
    people = [("mohamed", mine, "admin"), ("lukas", mine, "viewer"), ("wolf", theirs, "owner")]
    for username, workspace, role in people:
        user = User(username=username, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username, _, _ in people:
            http = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients, migrated
        finally:
            for http in clients.values():
                await http.aclose()


async def test_the_first_read_creates_it_switched_off(stage) -> None:
    clients, _ = stage
    body = (await clients["mohamed"].get("/api/channels/web")).json()

    # Off, with nothing allowed. On-by-default with an empty allowlist would refuse
    # every visitor while claiming to be on, which reads as broken rather than unset.
    assert body["enabled"] is False
    assert body["allowed_origins"] == []
    assert body["recaptcha_secret_preview"] is None
    # And it already has its address, so the snippet is copyable before anything else
    # is decided.
    assert len(body["embed_path"]) >= 20
    assert body["embed_path"] in body["embed_snippet"]
    assert body["embed_snippet"].startswith("<script src=")


async def test_the_snippet_points_at_the_installation_being_used(stage) -> None:
    """A hard-coded host is how a snippet ends up pointing at the developer's machine."""
    clients, _ = stage
    body = (await clients["mohamed"].get("/api/channels/web")).json()
    assert "http://localhost/embed.js" in body["embed_snippet"]


async def test_reading_twice_does_not_make_a_second_channel(stage) -> None:
    clients, db = stage
    first = (await clients["mohamed"].get("/api/channels/web")).json()
    second = (await clients["mohamed"].get("/api/channels/web")).json()
    assert first["embed_path"] == second["embed_path"]

    db.expire_all()
    rows = (await db.execute(select(Channel).where(Channel.kind == "web"))).scalars().all()
    assert len(rows) == 1


async def test_each_workspace_gets_its_own_address(stage) -> None:
    clients, _ = stage
    mine = (await clients["mohamed"].get("/api/channels/web")).json()
    theirs = (await clients["wolf"].get("/api/channels/web")).json()
    assert mine["embed_path"] != theirs["embed_path"]


async def test_origins_are_validated_by_the_function_that_enforces_them(stage) -> None:
    clients, _ = stage
    refused = await clients["mohamed"].put(
        "/api/channels/web", json={"allowed_origins": ["https://shop.test/checkout"]}
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "invalid_origin"


async def test_origins_are_stored_normalised_and_deduplicated(stage) -> None:
    clients, _ = stage
    body = (
        await clients["mohamed"].put(
            "/api/channels/web",
            json={
                "allowed_origins": [
                    "https://Shop.test",
                    "https://shop.test:443",
                    "http://localhost:3100",
                ]
            },
        )
    ).json()
    # The first two are the same origin written twice.
    assert body["allowed_origins"] == ["https://shop.test", "http://localhost:3100"]


async def test_it_cannot_be_switched_on_with_nothing_allowed(stage) -> None:
    clients, _ = stage
    refused = await clients["mohamed"].put("/api/channels/web", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "no_allowed_origins"

    # With an origin, it goes on.
    body = (
        await clients["mohamed"].put(
            "/api/channels/web",
            json={"enabled": True, "allowed_origins": ["https://shop.test"]},
        )
    ).json()
    assert body["enabled"] is True


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, db = stage
    body = (
        await clients["mohamed"].put(
            "/api/channels/web", json={"recaptcha_secret": "6Lc-the-real-secret-value"}
        )
    ).json()

    assert "recaptcha_secret" not in body
    assert body["recaptcha_secret_preview"].startswith("•")
    assert body["recaptcha_secret_preview"].endswith("alue")

    # Stored, and readable by the agent that has to use it.
    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.kind == "web"))
    assert row is not None
    assert row.credentials_encrypted == "6Lc-the-real-secret-value"


async def test_sending_the_mask_back_does_not_overwrite_the_secret(stage) -> None:
    """The screen renders the mask and saves the form. That must not be an edit."""
    clients, db = stage
    await clients["mohamed"].put(
        "/api/channels/web", json={"recaptcha_secret": "6Lc-the-real-secret-value"}
    )
    shown = (await clients["mohamed"].get("/api/channels/web")).json()[
        "recaptcha_secret_preview"
    ]

    await clients["mohamed"].put("/api/channels/web", json={"recaptcha_secret": shown})

    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.kind == "web"))
    assert row is not None
    assert row.credentials_encrypted == "6Lc-the-real-secret-value"


async def test_an_empty_string_removes_the_secret(stage) -> None:
    """Switching reCAPTCHA off has to be possible, and null means 'leave it alone'."""
    clients, db = stage
    await clients["mohamed"].put("/api/channels/web", json={"recaptcha_secret": "a-secret"})
    body = (
        await clients["mohamed"].put("/api/channels/web", json={"recaptcha_secret": ""})
    ).json()

    assert body["recaptcha_secret_preview"] is None
    db.expire_all()
    row = await db.scalar(select(Channel).where(Channel.kind == "web"))
    assert row is not None
    assert row.credentials_encrypted is None


async def test_a_partial_write_leaves_the_rest_alone(stage) -> None:
    clients, _ = stage
    await clients["mohamed"].put(
        "/api/channels/web",
        json={"allowed_origins": ["https://shop.test"], "recaptcha_site_key": "6Lc-site"},
    )
    body = (
        await clients["mohamed"].put("/api/channels/web", json={"recaptcha_threshold": 0.8})
    ).json()

    assert body["recaptcha_threshold"] == 0.8
    assert body["allowed_origins"] == ["https://shop.test"]
    assert body["recaptcha_site_key"] == "6Lc-site"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _ = stage
    assert (await clients["lukas"].get("/api/channels/web")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/web", json={"allowed_origins": []})
    ).status_code == 403


async def test_without_a_key_the_secret_is_refused_rather_than_exploding(
    stage, monkeypatch
) -> None:
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    clients, _ = stage
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    reset_key_cache()

    refused = await clients["mohamed"].put(
        "/api/channels/web", json={"recaptcha_secret": "a-secret"}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "encryption_key_missing"


async def test_what_is_configured_here_is_what_the_widget_endpoint_enforces(stage) -> None:
    """The whole point: this screen and §B14's guard read the same row."""
    clients, _ = stage
    body = (
        await clients["mohamed"].put(
            "/api/channels/web",
            json={"enabled": True, "allowed_origins": ["https://shop.test"]},
        )
    ).json()
    path = body["embed_path"]
    http = clients["mohamed"]

    allowed = await http.post(
        f"/public/chat/{path}/messages",
        json={"text": "hello"},
        headers={"Origin": "https://shop.test"},
    )
    assert allowed.status_code == 201

    refused = await http.post(
        f"/public/chat/{path}/messages",
        json={"text": "hello"},
        headers={"Origin": "https://elsewhere.test"},
    )
    assert refused.status_code == 403

    # And switching it off closes it, without touching the allowlist.
    await http.put("/api/channels/web", json={"enabled": False})
    closed = await http.post(
        f"/public/chat/{path}/messages",
        json={"text": "hello"},
        headers={"Origin": "https://shop.test"},
    )
    assert closed.status_code == 403
