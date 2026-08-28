"""Members and workspaces - the identity models get their endpoints.

Two rules carry the weight here and both are tested from every angle: the owner's
row is untouchable, and nobody acts on their own row. Everything else is the usual
discipline - a foreign id indistinguishable from a missing one, and one workspace
never seeing another.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import AuthEvent, Channel, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """The seed script's cast: one workspace with every role, plus an outsider.

    Yields plain user ids, not ORM objects - the fixture's commit expires them, and
    an expired attribute read mid-test is a lazy refresh the async session refuses.
    """
    first = Workspace(name="Wagner & Partner")
    second = Workspace(name="Wolf Studio")
    migrated.add_all([first, second])
    await migrated.flush()

    password_hash = hash_password(PASSWORD)
    people: dict[str, int] = {}
    for username, role in (
        ("wagner", "owner"),
        ("mohamed", "admin"),
        ("sabine", "reception"),
        ("julia", "invited"),
    ):
        user = User(username=username, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        people[username] = user.id
        migrated.add(Membership(user_id=user.id, workspace_id=first.id, role=role))

    outsider = User(username="wolf", password_hash=password_hash)
    migrated.add(outsider)
    await migrated.flush()
    people["wolf"] = outsider.id
    migrated.add(Membership(user_id=outsider.id, workspace_id=second.id, role="owner"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("wagner", "mohamed", "sabine", "wolf"):
            client = AsyncClient(transport=transport, base_url="http://localhost")
            response = await client.post(
                "/api/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert response.status_code == 200
            clients[username] = client
        try:
            yield people, clients
        finally:
            for client in clients.values():
                await client.aclose()


# --- Listing ------------------------------------------------------------------


async def test_the_team_is_listed_owner_first(stage) -> None:
    _people, clients = stage

    body = (await clients["mohamed"].get("/api/members")).json()

    assert [entry["username"] for entry in body] == ["wagner", "mohamed", "sabine", "julia"]
    assert [entry["role"] for entry in body] == ["owner", "admin", "reception", "invited"]


async def test_one_workspace_never_sees_another(stage) -> None:
    """D-028 at this table too."""
    _people, clients = stage

    body = (await clients["wolf"].get("/api/members")).json()

    assert [entry["username"] for entry in body] == ["wolf"]


async def test_reception_may_not_read_the_team(stage) -> None:
    """Managing users is the admin column of the role matrix, reading included -
    the list carries every colleague's email address."""
    _people, clients = stage

    response = await clients["sabine"].get("/api/members")

    assert response.status_code == 403
    assert "admin" in response.json()["error"]["message"]


# --- Changing a role ----------------------------------------------------------


async def test_an_admin_changes_a_role_and_the_trail_records_it(
    stage, migrated: AsyncSession
) -> None:
    people, clients = stage

    response = await clients["mohamed"].patch(
        f"/api/members/{people['sabine']}", json={"role": "viewer"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"

    migrated.expire_all()
    row = await migrated.scalar(
        select(Membership).where(Membership.user_id == people["sabine"])
    )
    assert row is not None
    assert row.role == "viewer"

    # Written against the affected account: the settings tab shows Sabine her own
    # trail, and "your role here changed" happened to her.
    event = await migrated.scalar(
        select(AuthEvent).where(
            AuthEvent.event == "role_changed", AuthEvent.user_id == people["sabine"]
        )
    )
    assert event is not None
    assert event.details["from"] == "reception"
    assert event.details["to"] == "viewer"


async def test_the_owner_row_is_untouchable(stage) -> None:
    people, clients = stage

    change = await clients["mohamed"].patch(
        f"/api/members/{people['wagner']}", json={"role": "viewer"}
    )
    remove = await clients["mohamed"].delete(f"/api/members/{people['wagner']}")

    assert change.status_code == 403
    assert remove.status_code == 403


async def test_nobody_acts_on_their_own_row(stage) -> None:
    """An admin who demotes or removes themselves mid-session leaves a workspace
    nobody may manage until the owner notices."""
    people, clients = stage

    change = await clients["mohamed"].patch(
        f"/api/members/{people['mohamed']}", json={"role": "viewer"}
    )
    remove = await clients["mohamed"].delete(f"/api/members/{people['mohamed']}")

    assert change.status_code == 403
    assert remove.status_code == 403


async def test_ownership_is_not_a_value_in_the_role_picker(stage) -> None:
    people, clients = stage

    response = await clients["mohamed"].patch(
        f"/api/members/{people['sabine']}", json={"role": "owner"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_role"


async def test_an_invitation_is_not_assigned_a_role(stage) -> None:
    """Assigning a role to a pending invitation would activate an account nobody has
    accepted. Cancelling is the action an invited row offers."""
    people, clients = stage

    response = await clients["mohamed"].patch(
        f"/api/members/{people['julia']}", json={"role": "viewer"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "still_invited"


async def test_a_foreign_member_is_indistinguishable_from_a_missing_one(stage) -> None:
    people, clients = stage

    foreign = await clients["mohamed"].patch(
        f"/api/members/{people['wolf']}", json={"role": "viewer"}
    )
    missing = await clients["mohamed"].patch("/api/members/999999", json={"role": "viewer"})

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


# --- Removing -----------------------------------------------------------------


async def test_removing_takes_the_membership_and_leaves_the_person(
    stage, migrated: AsyncSession
) -> None:
    people, clients = stage

    response = await clients["mohamed"].delete(f"/api/members/{people['sabine']}")

    assert response.status_code == 204

    migrated.expire_all()
    membership = await migrated.scalar(
        select(Membership).where(Membership.user_id == people["sabine"])
    )
    assert membership is None
    assert await migrated.get(User, people["sabine"]) is not None


async def test_cancelling_an_invite_is_the_same_delete(stage, migrated: AsyncSession) -> None:
    people, clients = stage

    response = await clients["mohamed"].delete(f"/api/members/{people['julia']}")

    assert response.status_code == 204
    migrated.expire_all()
    assert (
        await migrated.scalar(select(Membership).where(Membership.user_id == people["julia"]))
        is None
    )


# --- Creating a workspace -----------------------------------------------------


async def test_a_new_workspace_arrives_complete(stage, migrated: AsyncSession) -> None:
    """Workspace, owner membership and the `web` channel in one transaction - the
    same shape as first-run setup, because a half-created workspace is a state the
    interface cannot render."""
    people, clients = stage

    response = await clients["mohamed"].post(
        "/api/workspaces", json={"name": "Wagner Salzburg"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Wagner Salzburg"
    assert body["members"] == 1

    migrated.expire_all()
    membership = await migrated.scalar(
        select(Membership).where(
            Membership.workspace_id == body["id"], Membership.user_id == people["mohamed"]
        )
    )
    assert membership is not None
    assert membership.role == "owner"
    channel = await migrated.scalar(select(Channel).where(Channel.workspace_id == body["id"]))
    assert channel is not None
    assert channel.kind == "web"

    # And it reaches the switcher: /me lists it with the creator's new role.
    me = (await clients["mohamed"].get("/api/auth/me")).json()
    assert {"id": body["id"], "name": "Wagner Salzburg", "role": "owner"} in me["workspaces"]


async def test_include_team_copies_roles_with_the_two_stated_exceptions(
    stage, migrated: AsyncSession
) -> None:
    """Everyone keeps their role; the old owner arrives as admin (a workspace has one
    owner, and it is the creator); a pending invitation is not copied."""
    people, clients = stage

    response = await clients["mohamed"].post(
        "/api/workspaces", json={"name": "Wagner Salzburg", "include_team": True}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["members"] == 3  # mohamed + wagner + sabine; julia's invitation is not

    migrated.expire_all()
    rows = (
        await migrated.execute(
            select(Membership.user_id, Membership.role).where(
                Membership.workspace_id == body["id"]
            )
        )
    ).all()
    roles = dict(rows)
    assert roles[people["mohamed"]] == "owner"
    assert roles[people["wagner"]] == "admin"
    assert roles[people["sabine"]] == "reception"
    assert people["julia"] not in roles


async def test_a_name_is_unique_per_account_not_globally(stage) -> None:
    """The dialog's own copy: names are only for your own account. Wolf may call his
    workspace what Wagner calls theirs; Mohamed may not have two of the same name."""
    _people, clients = stage

    duplicate = await clients["mohamed"].post(
        "/api/workspaces", json={"name": "wagner & partner"}
    )
    someone_else = await clients["wolf"].post(
        "/api/workspaces", json={"name": "Wagner & Partner"}
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "workspace_name_taken"
    assert someone_else.status_code == 201


async def test_a_blank_name_is_refused(stage) -> None:
    _people, clients = stage

    response = await clients["mohamed"].post("/api/workspaces", json={"name": "  x "})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "name_too_short"


async def test_reception_may_not_create_or_manage(stage) -> None:
    people, clients = stage

    create = await clients["sabine"].post("/api/workspaces", json={"name": "Own thing"})
    change = await clients["sabine"].patch(
        f"/api/members/{people['julia']}", json={"role": "viewer"}
    )

    assert create.status_code == 403
    assert change.status_code == 403


async def test_signed_out_sees_nothing(stage) -> None:
    _people, clients = stage
    clients["mohamed"].cookies.clear()

    assert (await clients["mohamed"].get("/api/members")).status_code == 401
