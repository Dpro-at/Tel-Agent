"""The numbers registry: list, add, disable, release.

The tests that matter are isolation (a number id from another workspace must read
as missing), the role line (viewer reads, only admin writes), and the release
guard on `owner` - the one rule written before both kinds of number exist.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Membership, Number, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces holding numbers, and three roles to act with."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    rows = {
        "mine": Number(
            workspace_id=mine.id,
            provider="easybell",
            owner="customer",
            e164="+43720123456",
            status="active",
        ),
        "platform": Number(
            workspace_id=mine.id,
            provider="telagent-cloud",
            owner="platform",
            e164="+43720999999",
            status="active",
        ),
        "theirs": Number(
            workspace_id=theirs.id,
            provider="sipgate",
            owner="customer",
            e164="+4915790000000",
            status="active",
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
            yield clients, ids
        finally:
            for http in clients.values():
                await http.aclose()


async def test_list_is_scoped_to_the_workspace(stage) -> None:
    clients, _ = stage
    listed = await clients["mohamed"].get("/api/numbers")
    assert listed.status_code == 200
    assert [row["e164"] for row in listed.json()] == ["+43720123456", "+43720999999"]

    theirs = await clients["wolf"].get("/api/numbers")
    assert [row["e164"] for row in theirs.json()] == ["+4915790000000"]


async def test_a_viewer_reads_and_cannot_write(stage) -> None:
    clients, ids = stage
    assert (await clients["lukas"].get("/api/numbers")).status_code == 200

    added = await clients["lukas"].post(
        "/api/numbers", json={"e164": "+431234567", "provider": "easybell"}
    )
    assert added.status_code == 403
    changed = await clients["lukas"].patch(
        f"/api/numbers/{ids['mine']}", json={"status": "disabled"}
    )
    assert changed.status_code == 403
    assert (await clients["lukas"].delete(f"/api/numbers/{ids['mine']}")).status_code == 403


async def test_adding_normalises_and_validates_the_number(stage) -> None:
    clients, _ = stage
    added = await clients["mohamed"].post(
        "/api/numbers", json={"e164": "+43 1 402-8811", "provider": "  easybell  "}
    )
    assert added.status_code == 201
    body = added.json()
    assert body["e164"] == "+4314028811"
    assert body["provider"] == "easybell"
    assert body["owner"] == "customer"
    assert body["status"] == "active"

    for wrong in ("014028811", "+0431402", "+43123", "not a number"):
        refused = await clients["mohamed"].post(
            "/api/numbers", json={"e164": wrong, "provider": "easybell"}
        )
        assert refused.status_code == 400, wrong
        assert refused.json()["error"]["code"] == "invalid_e164"


async def test_the_same_number_cannot_be_added_twice(stage) -> None:
    clients, _ = stage
    again = await clients["mohamed"].post(
        "/api/numbers", json={"e164": "+43 720 123 456", "provider": "another"}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "number_taken"

    # The same digits in another workspace are somebody else's number to hold.
    elsewhere = await clients["wolf"].post(
        "/api/numbers", json={"e164": "+43720123456", "provider": "sipgate"}
    )
    assert elsewhere.status_code == 201


async def test_disable_and_enable(stage) -> None:
    clients, ids = stage
    disabled = await clients["mohamed"].patch(
        f"/api/numbers/{ids['mine']}", json={"status": "disabled"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = await clients["mohamed"].patch(
        f"/api/numbers/{ids['mine']}", json={"status": "active"}
    )
    assert enabled.json()["status"] == "active"

    refused = await clients["mohamed"].patch(
        f"/api/numbers/{ids['mine']}", json={"status": "on fire"}
    )
    assert refused.status_code == 400


async def test_release_removes_the_record(stage) -> None:
    clients, ids = stage
    gone = await clients["mohamed"].delete(f"/api/numbers/{ids['mine']}")
    assert gone.status_code == 204
    remaining = [row["e164"] for row in (await clients["mohamed"].get("/api/numbers")).json()]
    assert "+43720123456" not in remaining


async def test_a_platform_number_cannot_be_released_here(stage) -> None:
    clients, ids = stage
    refused = await clients["mohamed"].delete(f"/api/numbers/{ids['platform']}")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "platform_number"


async def test_a_foreign_id_reads_as_missing(stage) -> None:
    """The isolation rule: another workspace's number must be indistinguishable from
    one that does not exist, on every verb."""
    clients, ids = stage
    foreign = ids["theirs"]
    assert (
        await clients["mohamed"].patch(f"/api/numbers/{foreign}", json={"status": "disabled"})
    ).status_code == 404
    assert (await clients["mohamed"].delete(f"/api/numbers/{foreign}")).status_code == 404

    # And it was not touched: its own workspace still sees it active.
    theirs = (await clients["wolf"].get("/api/numbers")).json()
    assert theirs[0]["status"] == "active"
