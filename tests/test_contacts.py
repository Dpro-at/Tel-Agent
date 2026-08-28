"""The phonebook: list and search, reception writes, and the name reaching the
archive screens.

The tests that matter are isolation, the role line - reception is the writer here,
which is the first surface where that rank means something - and the enrichment in
both directions: the contact row saying when the number last called, and the
conversation thread carrying the contact's name.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Contact, Conversation, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces, contacts on both sides of a shared number, and three roles."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    phone = Channel(workspace_id=mine.id, kind="phone", name="Main line")
    migrated.add(phone)
    await migrated.flush()

    rows = {
        "gruber": Contact(
            workspace_id=mine.id,
            e164="+436641234567",
            name="Anna Gruber",
            tags=["customer"],
        ),
        "mayr": Contact(workspace_id=mine.id, e164="+4314028811", name="Elisabeth Mayr"),
        # The same number next door under another name - what a leak would show.
        "theirs": Contact(workspace_id=theirs.id, e164="+436641234567", name="Wolf Sees This"),
    }
    migrated.add_all(rows.values())
    await migrated.flush()
    ids = {name: row.id for name, row in rows.items()}

    conversation = Conversation(
        workspace_id=mine.id,
        channel_id=phone.id,
        external_id="+436641234567",
        direction="inbound",
        handling="ai",
    )
    migrated.add(conversation)

    password_hash = hash_password(PASSWORD)
    people = [
        ("sabine", mine, "reception"),
        ("lukas", mine, "viewer"),
        ("wolf", theirs, "owner"),
    ]
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


async def test_list_is_scoped_and_says_when_a_number_last_called(stage) -> None:
    clients, _ = stage
    page = (await clients["lukas"].get("/api/contacts")).json()
    names = [row["name"] for row in page["contacts"]]
    assert names == ["Anna Gruber", "Elisabeth Mayr"]
    assert page["has_more"] is False

    gruber, mayr = page["contacts"]
    assert gruber["last_heard_at"] is not None
    assert mayr["last_heard_at"] is None

    theirs = (await clients["wolf"].get("/api/contacts")).json()["contacts"]
    assert [row["name"] for row in theirs] == ["Wolf Sees This"]


async def test_search_by_name_and_by_number(stage) -> None:
    clients, _ = stage
    by_name = (await clients["lukas"].get("/api/contacts?q=gruber")).json()["contacts"]
    assert [row["name"] for row in by_name] == ["Anna Gruber"]

    by_number = (await clients["lukas"].get("/api/contacts?q=1402")).json()["contacts"]
    assert [row["name"] for row in by_number] == ["Elisabeth Mayr"]


async def test_reception_writes_and_a_viewer_cannot(stage) -> None:
    clients, ids = stage
    added = await clients["sabine"].post(
        "/api/contacts",
        json={
            "e164": "+43 650 771-4482",
            "name": "  Markus Steiner ",
            "tags": [" customer", "customer", "", "referral"],
            "notes": "Asked for a quote.",
        },
    )
    assert added.status_code == 201
    body = added.json()
    assert body["e164"] == "+436507714482"
    assert body["name"] == "Markus Steiner"
    assert body["tags"] == ["customer", "referral"]

    assert (
        await clients["lukas"].post(
            "/api/contacts", json={"e164": "+431111111", "name": "Nope"}
        )
    ).status_code == 403
    assert (
        await clients["lukas"].patch(f"/api/contacts/{ids['gruber']}", json={"name": "X"})
    ).status_code == 403
    assert (await clients["lukas"].delete(f"/api/contacts/{ids['gruber']}")).status_code == 403


async def test_one_contact_per_number(stage) -> None:
    clients, _ = stage
    again = await clients["sabine"].post(
        "/api/contacts", json={"e164": "+43 664 123 4567", "name": "Second Anna"}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "contact_exists"


async def test_editing_keeps_the_number(stage) -> None:
    clients, ids = stage
    changed = await clients["sabine"].patch(
        f"/api/contacts/{ids['gruber']}",
        json={"name": "Anna Gruber-Berg", "tags": ["customer", "vip"], "notes": None},
    )
    assert changed.status_code == 200
    assert changed.json()["name"] == "Anna Gruber-Berg"
    assert changed.json()["e164"] == "+436641234567"


async def test_the_archive_carries_the_name(stage) -> None:
    """The payoff: the conversation thread names the caller out of the phonebook,
    with the other workspace's identically numbered contact invisible."""
    clients, _ = stage
    threads = (await clients["lukas"].get("/api/conversations")).json()["threads"]
    assert threads[0]["who"] == "+436641234567"
    assert threads[0]["who_name"] == "Anna Gruber"

    detail = (await clients["lukas"].get(f"/api/conversations/{threads[0]['id']}")).json()
    assert detail["who_name"] == "Anna Gruber"


async def test_removing_a_contact_keeps_the_history(stage) -> None:
    clients, ids = stage
    assert (await clients["sabine"].delete(f"/api/contacts/{ids['gruber']}")).status_code == 204
    threads = (await clients["lukas"].get("/api/conversations")).json()["threads"]
    # The conversation is still there; only the name is gone.
    assert threads[0]["who"] == "+436641234567"
    assert threads[0]["who_name"] is None


async def test_a_foreign_id_reads_as_missing(stage) -> None:
    clients, ids = stage
    foreign = ids["theirs"]
    assert (
        await clients["sabine"].patch(f"/api/contacts/{foreign}", json={"name": "Taken"})
    ).status_code == 404
    assert (await clients["sabine"].delete(f"/api/contacts/{foreign}")).status_code == 404
    assert (await clients["wolf"].get("/api/contacts")).json()["contacts"][0][
        "name"
    ] == "Wolf Sees This"
