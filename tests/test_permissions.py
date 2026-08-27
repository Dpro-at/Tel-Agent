"""The five roles, enforced — P1.

Driven through real routes on a real app with the seed's own cast: one account per
role, plus a second workspace only the owner belongs to. The cross-workspace cases are
the ones that matter most — they are what D-028's isolation key exists for.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api import dependencies
from api.config import Settings
from api.main import create_app
from api.models import Membership, User, Workspace
from api.security.password import hash_password
from api.security.permissions import (
    CurrentWorkspace,
    require_admin,
    require_owner,
    require_reception,
    require_role,
    require_viewer,
)

PASSWORD = "a sentence i can actually remember"  # noqa: S105

ROLES = ("owner", "admin", "reception", "viewer", "invited")


@pytest.fixture
async def cast(migrated: AsyncSession):
    """One user per role in workspace A; the owner alone also in workspace B."""
    workspace_a = Workspace(name="Wagner & Partner")
    workspace_b = Workspace(name="Wolf Studio")
    migrated.add_all([workspace_a, workspace_b])
    await migrated.flush()

    users: dict[str, User] = {}
    password_hash = hash_password(PASSWORD)
    for role in ROLES:
        user = User(username=role, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        users[role] = user
        migrated.add(Membership(user_id=user.id, workspace_id=workspace_a.id, role=role))
    migrated.add(
        Membership(user_id=users["owner"].id, workspace_id=workspace_b.id, role="owner")
    )
    await migrated.commit()
    return workspace_a.id, workspace_b.id


@pytest.fixture
async def app_and_clients(
    cast, settings: Settings, database_url: str, monkeypatch: pytest.MonkeyPatch
):
    """An app with one probe route per rank, and a signed-in client per role."""
    monkeypatch.setattr(
        dependencies,
        "PUBLIC_PATHS",
        dependencies.PUBLIC_PATHS,  # probes are protected: that is the point
    )
    app = create_app(settings.model_copy(update={"database_url": database_url}))

    # The guard is attached as a route-level dependency and the context comes from a
    # module-importable annotation. With `from __future__ import annotations` in force,
    # a closure variable inside an Annotated[] cannot be resolved from the function's
    # globals, and FastAPI silently degrades the parameter to a required query field -
    # a trap worth this comment, because the symptom (422, "context missing") points
    # nowhere near the cause.
    async def probe(context: CurrentWorkspace) -> dict[str, object]:
        return {"workspace": context.id, "role": context.role}

    for name, guard in (
        ("viewer", require_viewer),
        ("reception", require_reception),
        ("admin", require_admin),
        ("owner", require_owner),
    ):
        app.get(f"/probe/{name}", dependencies=[guard])(probe)

    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for role in ROLES:
            client = AsyncClient(transport=transport, base_url="http://localhost")
            response = await client.post(
                "/api/auth/login", json={"username": role, "password": PASSWORD}
            )
            assert response.status_code == 200
            clients[role] = client
        try:
            yield cast, clients
        finally:
            for client in clients.values():
                await client.aclose()


# --- The ordering ------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "reaches"),
    [
        ("owner", {"viewer", "reception", "admin", "owner"}),
        ("admin", {"viewer", "reception", "admin"}),
        ("reception", {"viewer", "reception"}),
        ("viewer", {"viewer"}),
    ],
)
async def test_each_role_reaches_exactly_its_rank_and_below(
    app_and_clients, role: str, reaches: set[str]
) -> None:
    _ids, clients = app_and_clients

    for gate in ("viewer", "reception", "admin", "owner"):
        response = await clients[role].get(f"/probe/{gate}")
        expected = 200 if gate in reaches else 403
        assert response.status_code == expected, f"{role} at /probe/{gate}"


async def test_the_refusal_names_the_required_role(app_and_clients) -> None:
    """Signed in and refused is allowed to know why - unlike unauthenticated."""
    _ids, clients = app_and_clients

    response = await clients["viewer"].get("/probe/admin")

    assert response.status_code == 403
    assert "admin" in response.json()["error"]["message"]


# --- Invited: a pending fact, not a key --------------------------------------


async def test_an_invited_member_reaches_nothing(app_and_clients) -> None:
    """The interface lists invited people with a Cancel action, but until acceptance
    the invitation grants no reading of anything."""
    _ids, clients = app_and_clients

    for gate in ("viewer", "reception", "admin", "owner"):
        assert (await clients["invited"].get(f"/probe/{gate}")).status_code == 403


# --- Workspace scoping -------------------------------------------------------


async def test_a_member_acts_in_their_workspace_by_default(app_and_clients) -> None:
    (workspace_a, _b), clients = app_and_clients

    response = await clients["admin"].get("/probe/viewer")

    assert response.json() == {"workspace": workspace_a, "role": "admin"}


async def test_naming_a_workspace_you_do_not_belong_to_is_refused(
    app_and_clients,
) -> None:
    """The admin of workspace A holds no role at all in workspace B."""
    (_a, workspace_b), clients = app_and_clients

    response = await clients["admin"].get(
        "/probe/viewer", headers={"X-Workspace-Id": str(workspace_b)}
    )

    assert response.status_code == 403, response.text


async def test_a_missing_workspace_answers_like_a_foreign_one(app_and_clients) -> None:
    """403 for both, so which ids exist cannot be probed from the difference."""
    (_a, workspace_b), clients = app_and_clients

    foreign = await clients["admin"].get(
        "/probe/viewer", headers={"X-Workspace-Id": str(workspace_b)}
    )
    nonexistent = await clients["admin"].get(
        "/probe/viewer", headers={"X-Workspace-Id": "99999"}
    )

    assert foreign.status_code == nonexistent.status_code == 403
    assert foreign.json()["error"]["message"] == nonexistent.json()["error"]["message"]


async def test_the_owner_switches_workspaces_with_the_header(app_and_clients) -> None:
    """The sidebar's workspace switcher, as the API sees it."""
    (workspace_a, workspace_b), clients = app_and_clients

    stayed = await clients["owner"].get("/probe/owner")
    switched = await clients["owner"].get(
        "/probe/owner", headers={"X-Workspace-Id": str(workspace_b)}
    )

    assert stayed.json()["workspace"] == workspace_a
    assert switched.json()["workspace"] == workspace_b


# --- Misuse is loud ----------------------------------------------------------


def test_an_unknown_role_name_fails_at_import_time_not_at_request_time() -> None:
    with pytest.raises(ValueError):
        require_role("superuser")
