"""Routing rules: the three columns, and the archive line under each entry.

The tests that matter are isolation on every verb, the role line, the pattern
grammar (exact E.164 or a prefix ending in `*`, nothing else), and the last-call
enrichment - including the prefix rule that matches a call it never named exactly.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Call, Channel, Conversation, Membership, Rule, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


async def _call_from(
    db: AsyncSession, workspace: Workspace, channel: Channel, e164: str, *, hours_ago: int
) -> None:
    conversation = Conversation(
        workspace_id=workspace.id,
        channel_id=channel.id,
        direction="inbound",
        started_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours_ago),
        handling="ai",
        status="closed",
    )
    db.add(conversation)
    await db.flush()
    db.add(Call(conversation_id=conversation.id, workspace_id=workspace.id, from_e164=e164))


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces with rules and calls, and three roles to act with."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    phone = Channel(workspace_id=mine.id, kind="phone", name="Main line")
    other = Channel(workspace_id=theirs.id, kind="phone", name="Main line")
    migrated.add_all([phone, other])
    await migrated.flush()

    rules = {
        "exact": Rule(
            workspace_id=mine.id, pattern="+43664123456", action="pass", note="Staff"
        ),
        "prefix": Rule(workspace_id=mine.id, pattern="+43720*", action="block"),
        "theirs": Rule(workspace_id=theirs.id, pattern="+4915790000000", action="ai"),
    }
    migrated.add_all(rules.values())
    await migrated.flush()
    ids = {name: row.id for name, row in rules.items()}

    # Two calls the enrichment must find - one exact, one only a prefix names - and
    # one in the other workspace from the very number the prefix would match, which
    # is what makes the isolation of the enrichment testable.
    await _call_from(migrated, mine, phone, "+43664123456", hours_ago=5)
    await _call_from(migrated, mine, phone, "+43720555001", hours_ago=2)
    await _call_from(migrated, theirs, other, "+43720555999", hours_ago=1)

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


async def test_list_is_scoped_and_carries_the_last_call(stage) -> None:
    clients, _ = stage
    listed = (await clients["mohamed"].get("/api/rules")).json()
    assert [row["pattern"] for row in listed] == ["+43664123456", "+43720*"]

    exact, prefix = listed
    assert exact["last_called_at"] is not None
    assert exact["last_handling"] == "ai"
    # The prefix rule never names +43720555001 and still owns its call - while the
    # +43720555999 call next door must not leak into it.
    assert prefix["last_called_at"] is not None
    assert prefix["last_called_at"] > exact["last_called_at"]

    theirs = (await clients["wolf"].get("/api/rules")).json()
    assert [row["pattern"] for row in theirs] == ["+4915790000000"]
    # No call from that exact number is stored, so the line honestly says nothing.
    assert theirs[0]["last_called_at"] is None


async def test_a_viewer_reads_and_cannot_write(stage) -> None:
    clients, ids = stage
    assert (await clients["lukas"].get("/api/rules")).status_code == 200
    assert (
        await clients["lukas"].post(
            "/api/rules", json={"pattern": "+431234567", "action": "block"}
        )
    ).status_code == 403
    assert (
        await clients["lukas"].patch(f"/api/rules/{ids['exact']}", json={"action": "block"})
    ).status_code == 403
    assert (await clients["lukas"].delete(f"/api/rules/{ids['exact']}")).status_code == 403


async def test_the_pattern_grammar(stage) -> None:
    clients, _ = stage
    added = await clients["mohamed"].post(
        "/api/rules",
        json={"pattern": "+43 1 402-8811", "action": "ai", "note": "  Customers  "},
    )
    assert added.status_code == 201
    assert added.json()["pattern"] == "+4314028811"
    assert added.json()["note"] == "Customers"

    prefix = await clients["mohamed"].post(
        "/api/rules", json={"pattern": "+49 157*", "action": "block"}
    )
    assert prefix.status_code == 201
    assert prefix.json()["pattern"] == "+49157*"

    # Milestone 4 widened the grammar: an identity - an email, a handle, a bare
    # numeric chat id - is a valid pattern now, stored lowercased. What stays
    # refused is whitespace and a star anywhere but the end.
    for identity, stored in (
        ("Boss@Example.com", "boss@example.com"),
        ("@hans_maulwurf", "@hans_maulwurf"),
        ("014028811", "014028811"),
    ):
        added = await clients["mohamed"].post(
            "/api/rules", json={"pattern": identity, "action": "block"}
        )
        assert added.status_code == 201, identity
        assert added.json()["pattern"] == stored

    for wrong in ("*43720", "+43*720", "group: customers", "a b"):
        refused = await clients["mohamed"].post(
            "/api/rules", json={"pattern": wrong, "action": "block"}
        )
        assert refused.status_code == 400, wrong
        assert refused.json()["error"]["code"] == "invalid_pattern"

    action = await clients["mohamed"].post(
        "/api/rules", json={"pattern": "+43111222333", "action": "shout"}
    )
    assert action.status_code == 400
    assert action.json()["error"]["code"] == "invalid_action"


async def test_one_rule_per_pattern(stage) -> None:
    clients, _ = stage
    again = await clients["mohamed"].post(
        "/api/rules", json={"pattern": "+43 664 123 456", "action": "block"}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "rule_exists"


async def test_moving_between_columns(stage) -> None:
    clients, ids = stage
    moved = await clients["mohamed"].patch(
        f"/api/rules/{ids['exact']}", json={"action": "ai", "note": "Let the agent try"}
    )
    assert moved.status_code == 200
    assert moved.json()["action"] == "ai"
    assert moved.json()["note"] == "Let the agent try"

    refused = await clients["mohamed"].patch(
        f"/api/rules/{ids['exact']}", json={"action": "shout"}
    )
    assert refused.status_code == 400


async def test_removing_a_rule(stage) -> None:
    clients, ids = stage
    assert (await clients["mohamed"].delete(f"/api/rules/{ids['prefix']}")).status_code == 204
    listed = (await clients["mohamed"].get("/api/rules")).json()
    assert "+43720*" not in [row["pattern"] for row in listed]


async def test_a_foreign_id_reads_as_missing(stage) -> None:
    clients, ids = stage
    foreign = ids["theirs"]
    assert (
        await clients["mohamed"].patch(f"/api/rules/{foreign}", json={"action": "block"})
    ).status_code == 404
    assert (await clients["mohamed"].delete(f"/api/rules/{foreign}")).status_code == 404
    # And it was not touched.
    assert (await clients["wolf"].get("/api/rules")).json()[0]["action"] == "ai"
