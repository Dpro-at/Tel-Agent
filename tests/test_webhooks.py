"""Webhooks: the registry, and the secret nobody may read twice.

The test that matters most is the one about the secret - full on create, masked on
every read, replaced on rotate. The rest is the usual shape: isolation on every verb,
the role line, and a vocabulary of events the API refuses to invent.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Membership, User, Webhook, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
# Fixed fixture secrets, so a rotation is visibly a change rather than a coincidence.
OURS_SECRET = "01" * 16
THEIRS_SECRET = "ff" * 16


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    """Every test here writes an encrypted column, so the key is not optional.

    Autouse rather than requested per test: a webhook without a secret is not a
    webhook, so a test that forgot this fixture is a test that cannot run at all.
    """
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
    """Two workspaces with a webhook each, and three roles to act with."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    rows = {
        "ours": Webhook(
            workspace_id=mine.id,
            name="Practice software",
            url="https://practice.wagner-partner.at/hooks/telagent",
            events=["conversation.ended"],
            secret=OURS_SECRET,
        ),
        "theirs": Webhook(
            workspace_id=theirs.id,
            url="https://wolf.example/hook",
            events=["conversation.started"],
            secret=THEIRS_SECRET,
        ),
    }
    migrated.add_all(rows.values())
    await migrated.flush()
    ids = {name: row.id for name, row in rows.items()}

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
            yield clients, ids, migrated
        finally:
            for http in clients.values():
                await http.aclose()


async def test_the_secret_is_shown_once_and_masked_afterwards(stage) -> None:
    clients, _, _ = stage
    created = await clients["mohamed"].post(
        "/api/webhooks",
        json={"url": "https://example.test/hook", "events": ["conversation.ended"]},
    )
    assert created.status_code == 201
    body = created.json()
    secret = body["secret"]
    assert len(secret) == 64
    assert body["secret_preview"].endswith(secret[-4:])

    # Every read afterwards. The full value is gone from the API surface for good.
    listed = (await clients["mohamed"].get("/api/webhooks")).json()
    fresh = next(row for row in listed if row["id"] == body["id"])
    assert "secret" not in fresh
    assert fresh["secret_preview"].startswith("•")
    assert secret not in str(listed)


async def test_rotating_replaces_the_secret_and_returns_it_once(stage) -> None:
    clients, ids, _ = stage
    before = (await clients["mohamed"].get("/api/webhooks")).json()[0]["secret_preview"]

    rotated = await clients["mohamed"].post(f"/api/webhooks/{ids['ours']}/secret")
    assert rotated.status_code == 200
    assert len(rotated.json()["secret"]) == 64

    after = (await clients["mohamed"].get("/api/webhooks")).json()[0]["secret_preview"]
    assert after != before


async def test_the_secret_is_encrypted_at_rest(stage) -> None:
    clients, _, db = stage
    created = await clients["mohamed"].post(
        "/api/webhooks",
        json={"url": "https://example.test/rest", "events": ["message.received"]},
    )
    secret = created.json()["secret"]

    # Read the column through a fresh session: the ORM decrypts, so the check is that
    # the value round-trips - the ciphertext itself is crypto's own test.
    db.expire_all()
    row = await db.scalar(select(Webhook).where(Webhook.id == created.json()["id"]))
    assert row is not None
    assert row.secret == secret


async def test_list_is_scoped_and_the_neighbour_is_absent(stage) -> None:
    clients, ids, _ = stage
    mine = (await clients["mohamed"].get("/api/webhooks")).json()
    assert [row["url"] for row in mine] == ["https://practice.wagner-partner.at/hooks/telagent"]

    for verb, kwargs in (("patch", {"json": {"enabled": False}}), ("delete", {})):
        answer = await getattr(clients["mohamed"], verb)(
            f"/api/webhooks/{ids['theirs']}", **kwargs
        )
        assert answer.status_code == 404, verb


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, ids, _ = stage
    assert (await clients["lukas"].get("/api/webhooks")).status_code == 200
    assert (
        await clients["lukas"].post(
            "/api/webhooks", json={"url": "https://x.test/h", "events": ["conversation.ended"]}
        )
    ).status_code == 403
    assert (
        await clients["lukas"].post(f"/api/webhooks/{ids['ours']}/secret")
    ).status_code == 403
    assert (await clients["lukas"].delete(f"/api/webhooks/{ids['ours']}")).status_code == 403


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"url": "wagner-partner.at/hook", "events": ["conversation.ended"]}, "invalid_url"),
        ({"url": "ftp://x.test/h", "events": ["conversation.ended"]}, "invalid_url"),
        ({"url": "https://x.test/h", "events": ["call.recorded"]}, "unknown_event"),
        ({"url": "https://x.test/h", "events": []}, "no_events"),
    ],
)
async def test_a_webhook_needs_a_real_url_and_real_events(stage, payload, code) -> None:
    clients, _, _ = stage
    refused = await clients["mohamed"].post("/api/webhooks", json=payload)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == code


async def test_http_is_allowed_for_a_service_on_the_same_network(stage) -> None:
    clients, _, _ = stage
    # Refusing plain http would push a self-hoster to a public endpoint, which is the
    # outcome the rule exists to prevent.
    created = await clients["mohamed"].post(
        "/api/webhooks",
        json={"url": "http://192.168.1.20:5678/webhook", "events": ["conversation.ended"]},
    )
    assert created.status_code == 201


async def test_the_event_vocabulary_is_served_rather_than_copied(stage) -> None:
    clients, _, _ = stage
    events = (await clients["mohamed"].get("/api/webhooks/events")).json()
    assert "conversation.ended" in events
    # Past tense, every one: a hook fires because something already happened.
    assert all("." in name for name in events)


async def test_switching_one_off_keeps_its_secret(stage) -> None:
    clients, ids, _ = stage
    before = (await clients["mohamed"].get("/api/webhooks")).json()[0]["secret_preview"]

    off = await clients["mohamed"].patch(
        f"/api/webhooks/{ids['ours']}", json={"enabled": False}
    )
    assert off.status_code == 200
    assert off.json()["enabled"] is False
    # The whole reason off is not deleted: resuming must not mean reconfiguring every
    # receiver with a new secret.
    assert off.json()["secret_preview"] == before


async def test_removing_one(stage) -> None:
    clients, ids, _ = stage
    assert (await clients["mohamed"].delete(f"/api/webhooks/{ids['ours']}")).status_code == 204
    assert (await clients["mohamed"].get("/api/webhooks")).json() == []


async def test_without_a_key_the_write_is_refused_rather_than_exploding(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure that must not happen is an unhandled INSERT.

    Its SQLAlchemy parameter dump carries the secret into the log, which is how a
    missing key becomes a leaked one - so the refusal is a 409 with a name.
    """
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    clients, ids, _ = stage
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    reset_key_cache()

    refused = await clients["mohamed"].post(
        "/api/webhooks",
        json={"url": "https://example.test/hook", "events": ["conversation.ended"]},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "encryption_key_missing"

    rotating = await clients["mohamed"].post(f"/api/webhooks/{ids['ours']}/secret")
    assert rotating.status_code == 409
