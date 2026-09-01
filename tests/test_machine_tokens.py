"""The credentials the machine paths use, and the gate that reads them.

§B9.1 names three paths that can start a real call and gives each its own credential:
the dashboard session, the inbound webhook, and the MCP endpoint. The sentence that
these tests exist for is the one after the table — *a leak of one must not open the
others* — so the pairs matter more than any single case here: an MCP token at the
webhook path, a machine token at a dashboard route, and a dashboard session at a
machine path all have to be refused, and refused identically.
"""

from __future__ import annotations

import hashlib

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import MachineToken, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces, and three roles to act with."""
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
        # A client with no cookie of its own, for everything a machine does.
        machine = AsyncClient(transport=transport, base_url="http://localhost")
        try:
            yield clients, machine, migrated
        finally:
            await machine.aclose()
            for http in clients.values():
                await http.aclose()


async def _mint(clients, scope: str = "hooks", name: str = "Provider") -> str:
    created = await clients["mohamed"].post("/api/tokens", json={"name": name, "scope": scope})
    assert created.status_code == 201, created.text
    return created.json()["token"]


async def test_the_token_is_shown_once_and_only_its_last_four_afterwards(stage) -> None:
    clients, _, _ = stage
    created = await clients["mohamed"].post(
        "/api/tokens", json={"name": "Provider", "scope": "hooks"}
    )
    assert created.status_code == 201
    body = created.json()
    token = body["token"]

    listed = (await clients["mohamed"].get("/api/tokens")).json()
    row = next(entry for entry in listed if entry["id"] == body["id"])
    assert "token" not in row
    assert row["last_four"] == token[-4:]
    assert token not in str(listed)


async def test_only_the_hash_of_the_token_is_stored(stage) -> None:
    clients, _, db = stage
    token = await _mint(clients)

    stored = (await db.execute(select(MachineToken))).scalars().all()
    assert len(stored) == 1
    assert stored[0].token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(stored[0].__dict__)


async def test_the_scope_is_a_closed_vocabulary(stage) -> None:
    clients, _, _ = stage
    refused = await clients["mohamed"].post(
        "/api/tokens", json={"name": "Anything", "scope": "everything"}
    )
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "unknown_scope"


async def test_a_viewer_cannot_mint_a_token(stage) -> None:
    clients, _, _ = stage
    refused = await clients["lukas"].post("/api/tokens", json={"name": "Mine", "scope": "mcp"})
    assert refused.status_code == 403


async def test_another_workspaces_token_is_neither_listed_nor_removable(stage) -> None:
    clients, _, _ = stage
    created = await clients["mohamed"].post(
        "/api/tokens", json={"name": "Ours", "scope": "hooks"}
    )
    token_id = created.json()["id"]

    assert (await clients["wolf"].get("/api/tokens")).json() == []
    assert (await clients["wolf"].delete(f"/api/tokens/{token_id}")).status_code == 404
    assert (await clients["wolf"].post(f"/api/tokens/{token_id}/rotate")).status_code == 404


# The gate. `/hooks/…` is not served yet — the webhook receiver arrives with the
# phone — so a request that gets past the gate there ends in a 404. That is a real
# assertion: 404 means the credential was accepted and routing simply had nowhere to
# send it, which is exactly what the path must do until it exists. `/mcp` *is* served
# (Milestone 7), so its post-gate answer is the endpoint's own; `tests/test_mcp.py`
# owns everything past that door.


async def test_a_machine_path_without_a_token_is_refused(stage) -> None:
    _, machine, _ = stage
    refused = await machine.post("/hooks/call", json={})
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "machine_token_required"


async def test_a_token_gets_past_the_gate_to_a_path_that_is_not_served_yet(stage) -> None:
    clients, machine, _ = stage
    token = await _mint(clients, "hooks")

    got = await machine.post(
        "/hooks/call", json={}, headers={"Authorization": f"Bearer {token}"}
    )
    assert got.status_code == 404


async def test_a_token_for_one_path_does_not_open_the_other(stage) -> None:
    """§B9.1: a leak of one must not open the others — nor confirm that it is real."""
    clients, machine, _ = stage
    mcp = await _mint(clients, "mcp", name="A model")

    wrong = await machine.post("/hooks/call", headers={"Authorization": f"Bearer {mcp}"})
    unknown = await machine.post(
        "/hooks/call", headers={"Authorization": "Bearer telagent_hooks_nothing"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]

    # And the token does open the path it was minted for: past the gate, the MCP
    # endpoint answers for itself - an empty body is a JSON-RPC parse error, not a
    # refusal at the door.
    opened = await machine.post("/mcp", headers={"Authorization": f"Bearer {mcp}"})
    assert opened.status_code == 200
    assert opened.json()["error"]["code"] == -32700


async def test_a_removed_token_stops_working(stage) -> None:
    clients, machine, _ = stage
    created = await clients["mohamed"].post(
        "/api/tokens", json={"name": "Provider", "scope": "hooks"}
    )
    token = created.json()["token"]
    header = {"Authorization": f"Bearer {token}"}
    assert (await machine.post("/hooks/call", headers=header)).status_code == 404

    assert (
        await clients["mohamed"].delete(f"/api/tokens/{created.json()['id']}")
    ).status_code == 204
    assert (await machine.post("/hooks/call", headers=header)).status_code == 401


async def test_rotating_replaces_the_token_and_the_old_one_stops_working(stage) -> None:
    clients, machine, _ = stage
    created = await clients["mohamed"].post(
        "/api/tokens", json={"name": "Provider", "scope": "hooks"}
    )
    old = created.json()["token"]

    rotated = await clients["mohamed"].post(f"/api/tokens/{created.json()['id']}/rotate")
    assert rotated.status_code == 200
    new = rotated.json()["token"]
    assert new != old

    assert (
        await machine.post("/hooks/call", headers={"Authorization": f"Bearer {old}"})
    ).status_code == 401
    assert (
        await machine.post("/hooks/call", headers={"Authorization": f"Bearer {new}"})
    ).status_code == 404
    # The row survived rotation, so what it was doing is still on the screen.
    listed = (await clients["mohamed"].get("/api/tokens")).json()
    assert [row["name"] for row in listed] == ["Provider"]
    assert listed[0]["last_four"] == new[-4:]


async def test_the_dashboard_session_does_not_open_a_machine_path(stage) -> None:
    """The cookie is a different credential for a different door."""
    clients, _, _ = stage
    refused = await clients["mohamed"].post("/hooks/call", json={})
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "machine_token_required"


async def test_a_machine_token_does_not_open_a_dashboard_route(stage) -> None:
    clients, machine, _ = stage
    token = await _mint(clients, "hooks")

    refused = await machine.get("/api/tokens", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "unauthenticated"


async def test_use_is_recorded_so_a_forgotten_credential_is_visible(stage) -> None:
    clients, machine, _ = stage
    created = await clients["mohamed"].post(
        "/api/tokens", json={"name": "Provider", "scope": "hooks"}
    )
    assert created.json()["last_used_at"] is None

    await machine.post(
        "/hooks/call", headers={"Authorization": f"Bearer {created.json()['token']}"}
    )
    listed = (await clients["mohamed"].get("/api/tokens")).json()
    assert listed[0]["last_used_at"] is not None


async def test_a_token_over_its_ceiling_is_refused(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as datetime_

    from api.security import machine_tokens
    from api.security.quota import Limit

    clients, machine, _ = stage
    token = await _mint(clients, "hooks")
    header = {"Authorization": f"Bearer {token}"}
    monkeypatch.setitem(
        machine_tokens.PER_TOKEN, "hooks", Limit(count=2, window=datetime_.timedelta(minutes=1))
    )

    assert (await machine.post("/hooks/call", headers=header)).status_code == 404
    assert (await machine.post("/hooks/call", headers=header)).status_code == 404
    over = await machine.post("/hooks/call", headers=header)
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "rate_limited"


async def test_guessing_is_limited_before_anything_is_proved(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    import datetime as datetime_

    from api.security import machine_tokens
    from api.security.quota import Limit

    _, machine, _ = stage
    monkeypatch.setattr(
        machine_tokens, "PER_CLIENT", Limit(count=2, window=datetime_.timedelta(minutes=1))
    )

    header = {"Authorization": "Bearer telagent_hooks_guess"}
    assert (await machine.post("/hooks/call", headers=header)).status_code == 401
    assert (await machine.post("/hooks/call", headers=header)).status_code == 401
    assert (await machine.post("/hooks/call", headers=header)).status_code == 429
