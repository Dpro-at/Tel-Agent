"""Knowledge: the sources, who may change them, and which assistant may read them.

The tests that matter are isolation on every verb, the role line, and the two things
`assistant_id` has to get right - a foreign workspace's assistant refused even though
the row exists, and an assistant's deletion leaving the text behind rather than taking
it along.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Assistant, Knowledge, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces, an assistant in each, and knowledge on both sides."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    lena = Assistant(workspace_id=mine.id, name="Lena", template="reception")
    wolf = Assistant(workspace_id=theirs.id, name="Wolf", template="blank")
    migrated.add_all([lena, wolf])
    await migrated.flush()

    rows = {
        # No assistant: every assistant here may read it, which is most knowledge.
        "hours": Knowledge(
            workspace_id=mine.id, title="Opening hours", content="Mon-Fri 08:00-17:00"
        ),
        "prices": Knowledge(
            workspace_id=mine.id,
            assistant_id=lena.id,
            title="Price list",
            content="Consultation 90 EUR",
        ),
        "theirs": Knowledge(
            workspace_id=theirs.id, title="Studio rates", content="Half day 400 EUR"
        ),
    }
    migrated.add_all(rows.values())
    await migrated.flush()
    ids = {name: row.id for name, row in rows.items()}
    ids["lena"] = lena.id
    ids["wolf_assistant"] = wolf.id

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


async def test_list_is_scoped_and_names_the_assistant(stage) -> None:
    clients, _ = stage
    listed = (await clients["mohamed"].get("/api/knowledge")).json()
    assert [row["title"] for row in listed] == ["Opening hours", "Price list"]

    hours, prices = listed
    # Null is not a gap in the data - it is "every assistant here".
    assert hours["assistant_id"] is None
    assert hours["assistant_name"] is None
    assert prices["assistant_name"] == "Lena"

    assert [row["title"] for row in (await clients["wolf"].get("/api/knowledge")).json()] == [
        "Studio rates"
    ]


async def test_another_workspaces_source_is_simply_absent(stage) -> None:
    clients, ids = stage
    for verb, kwargs in (("patch", {"json": {"title": "Taken"}}), ("delete", {})):
        answer = await getattr(clients["mohamed"], verb)(
            f"/api/knowledge/{ids['theirs']}", **kwargs
        )
        assert answer.status_code == 404, verb
        assert answer.json()["error"]["code"] == "not_found"


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, ids = stage
    assert (await clients["lukas"].get("/api/knowledge")).status_code == 200
    assert (
        await clients["lukas"].post("/api/knowledge", json={"title": "X", "content": "y"})
    ).status_code == 403
    assert (
        await clients["lukas"].patch(f"/api/knowledge/{ids['hours']}", json={"title": "X"})
    ).status_code == 403
    assert (await clients["lukas"].delete(f"/api/knowledge/{ids['hours']}")).status_code == 403


async def test_adding_a_source(stage) -> None:
    clients, ids = stage
    created = await clients["mohamed"].post(
        "/api/knowledge",
        json={
            "title": "  Parking  ",
            "content": "Two spaces behind the building.",
            "assistant_id": ids["lena"],
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Parking"
    assert body["assistant_name"] == "Lena"


async def test_a_foreign_assistant_cannot_be_attached(stage) -> None:
    clients, ids = stage
    # The row exists, in the other workspace. The foreign key alone would accept it,
    # which is exactly why the check is against the workspace and not the table.
    refused = await clients["mohamed"].post(
        "/api/knowledge",
        json={"title": "Smuggled", "content": "x", "assistant_id": ids["wolf_assistant"]},
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "no_such_assistant"

    also_refused = await clients["mohamed"].patch(
        f"/api/knowledge/{ids['hours']}", json={"assistant_id": ids["wolf_assistant"]}
    )
    assert also_refused.status_code == 400


@pytest.mark.parametrize(
    ("payload", "status_code"),
    [
        ({"title": "   ", "content": "x"}, 400),
        ({"title": "Empty", "content": ""}, 422),
    ],
)
async def test_a_source_needs_a_title_and_some_text(stage, payload, status_code) -> None:
    clients, _ = stage
    assert (
        await clients["mohamed"].post("/api/knowledge", json=payload)
    ).status_code == status_code


async def test_a_patch_touches_only_what_it_names(stage) -> None:
    clients, ids = stage
    before = (await clients["mohamed"].get("/api/knowledge")).json()[1]

    patched = await clients["mohamed"].patch(
        f"/api/knowledge/{ids['prices']}", json={"content": "Consultation 120 EUR"}
    )
    assert patched.status_code == 200
    assert patched.json()["content"] == "Consultation 120 EUR"
    assert patched.json()["title"] == before["title"]
    assert patched.json()["assistant_id"] == before["assistant_id"]


async def test_assistant_id_sent_as_null_means_every_assistant(stage) -> None:
    clients, ids = stage
    cleared = await clients["mohamed"].patch(
        f"/api/knowledge/{ids['prices']}", json={"assistant_id": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["assistant_id"] is None
    assert cleared.json()["assistant_name"] is None


async def test_deleting_the_assistant_leaves_the_text(stage) -> None:
    clients, ids = stage
    assert (
        await clients["mohamed"].delete(f"/api/assistants/{ids['lena']}")
    ).status_code == 204

    # The price list is still there, and now belongs to every assistant rather than
    # to a name that no longer exists.
    listed = (await clients["mohamed"].get("/api/knowledge")).json()
    prices = next(row for row in listed if row["title"] == "Price list")
    assert prices["assistant_id"] is None
    assert prices["content"] == "Consultation 90 EUR"


async def test_removing_a_source(stage) -> None:
    clients, ids = stage
    assert (
        await clients["mohamed"].delete(f"/api/knowledge/{ids['hours']}")
    ).status_code == 204
    assert [
        row["title"] for row in (await clients["mohamed"].get("/api/knowledge")).json()
    ] == ["Price list"]
