"""Assistants: the list, the editor's saves, and who is allowed to make them.

The tests that matter are isolation on every verb, the role line (a viewer reads and
never writes), the unique name inside a workspace and *only* inside it, and the patch
semantics the editor depends on - one panel at a time, where an absent field is left
alone and a field sent as null is cleared.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Assistant, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces with assistants, and three roles to act with."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    assistants = {
        "lena": Assistant(
            workspace_id=mine.id,
            name="Lena",
            role="Reception, weekdays",
            template="reception",
            persona="You answer for Wagner & Partner.",
            instructions="Never quote a price that is not in the catalogue.",
        ),
        "nacht": Assistant(
            workspace_id=mine.id, name="Nacht", template="ooh", status="paused"
        ),
        # The same name next door: the uniqueness is per workspace, and a test that
        # does not prove that is a test that would pass against a global constraint.
        "theirs": Assistant(workspace_id=theirs.id, name="Lena", template="blank"),
    }
    migrated.add_all(assistants.values())
    await migrated.flush()
    ids = {name: row.id for name, row in assistants.items()}

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
    mine = (await clients["mohamed"].get("/api/assistants")).json()
    assert [row["name"] for row in mine] == ["Lena", "Nacht"]

    # The neighbour has a "Lena" too, and sees only their own.
    theirs = (await clients["wolf"].get("/api/assistants")).json()
    assert [row["name"] for row in theirs] == ["Lena"]
    assert theirs[0]["id"] != mine[0]["id"]


async def test_detail_carries_both_texts(stage) -> None:
    clients, ids = stage
    row = (await clients["mohamed"].get(f"/api/assistants/{ids['lena']}")).json()
    assert row["persona"].startswith("You answer for")
    assert row["instructions"].startswith("Never quote")
    assert row["template"] == "reception"
    assert row["status"] == "active"


async def test_another_workspaces_assistant_is_simply_absent(stage) -> None:
    clients, ids = stage
    for verb, kwargs in (
        ("get", {}),
        ("patch", {"json": {"name": "Taken over"}}),
        ("delete", {}),
    ):
        answer = await getattr(clients["mohamed"], verb)(
            f"/api/assistants/{ids['theirs']}", **kwargs
        )
        # 404, not 403: telling one customer that another customer's id exists is
        # itself the leak.
        assert answer.status_code == 404, verb
        assert answer.json()["error"]["code"] == "not_found"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, ids = stage
    assert (await clients["lukas"].get("/api/assistants")).status_code == 200
    assert (
        await clients["lukas"].post("/api/assistants", json={"name": "Smuggled"})
    ).status_code == 403
    assert (
        await clients["lukas"].patch(
            f"/api/assistants/{ids['lena']}", json={"persona": "rewritten"}
        )
    ).status_code == 403
    assert (
        await clients["lukas"].delete(f"/api/assistants/{ids['lena']}")
    ).status_code == 403


async def test_creating_one_and_reading_it_back(stage) -> None:
    clients, _ = stage
    created = await clients["mohamed"].post(
        "/api/assistants",
        json={"name": "  Tag  ", "role": "Overflow", "template": "overflow"},
    )
    assert created.status_code == 201
    body = created.json()
    # The name is stored trimmed: " Tag " and "Tag" are the same assistant to
    # everyone except a unique constraint.
    assert body["name"] == "Tag"
    assert body["status"] == "active"
    assert body["persona"] == ""

    listed = (await clients["mohamed"].get("/api/assistants")).json()
    assert [row["name"] for row in listed] == ["Lena", "Nacht", "Tag"]


async def test_the_name_is_unique_inside_a_workspace_only(stage) -> None:
    clients, _ = stage
    clash = await clients["mohamed"].post("/api/assistants", json={"name": "Lena"})
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "name_taken"

    # Next door the same name is free, because it is a different workspace.
    assert (
        await clients["wolf"].post("/api/assistants", json={"name": "Nacht"})
    ).status_code == 201


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"name": "Nameless", "template": "wildcard"}, "invalid_template"),
        ({"name": "   "}, "invalid_name"),
    ],
)
async def test_the_typed_fields_are_checked(stage, payload, code) -> None:
    clients, _ = stage
    refused = await clients["mohamed"].post("/api/assistants", json=payload)
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == code


async def test_a_patch_touches_only_what_it_names(stage) -> None:
    clients, ids = stage
    before = (await clients["mohamed"].get(f"/api/assistants/{ids['lena']}")).json()

    patched = await clients["mohamed"].patch(
        f"/api/assistants/{ids['lena']}", json={"instructions": "Hand invoices to a person."}
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["instructions"] == "Hand invoices to a person."
    # The panel that was not sent is the panel that did not change - this is what
    # lets the editor save one panel without holding the whole form.
    assert body["persona"] == before["persona"]
    assert body["name"] == before["name"]
    assert body["template"] == before["template"]


async def test_a_field_sent_as_null_is_cleared(stage) -> None:
    clients, ids = stage
    cleared = await clients["mohamed"].patch(
        f"/api/assistants/{ids['lena']}", json={"role": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["role"] is None


async def test_renaming_onto_a_taken_name_is_refused(stage) -> None:
    clients, ids = stage
    clash = await clients["mohamed"].patch(
        f"/api/assistants/{ids['nacht']}", json={"name": "Lena"}
    )
    assert clash.status_code == 409

    # Renaming an assistant to the name it already has is not a clash with itself.
    same = await clients["mohamed"].patch(
        f"/api/assistants/{ids['lena']}", json={"name": "Lena"}
    )
    assert same.status_code == 200


async def test_pausing_and_deleting(stage) -> None:
    clients, ids = stage
    paused = await clients["mohamed"].patch(
        f"/api/assistants/{ids['lena']}", json={"status": "paused"}
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    assert (
        await clients["mohamed"].delete(f"/api/assistants/{ids['lena']}")
    ).status_code == 204
    assert (
        await clients["mohamed"].get(f"/api/assistants/{ids['lena']}")
    ).status_code == 404

    # And the name it held is free again, which is the whole reason the delete is
    # real rather than a flag.
    assert (
        await clients["mohamed"].post("/api/assistants", json={"name": "Lena"})
    ).status_code == 201
