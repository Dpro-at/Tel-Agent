"""The two numbers the home screen opens with.

The one worth testing hardest is `by_agent`. It is null when nothing recorded who took
a conversation and a number when something did, and the difference between those two is
the difference between "we are not measuring this yet" and "the agent handled none of
them" - which is a sentence about the product being broken.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Notification, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


def _ago(hours: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(hours=hours)


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    password_hash = hash_password(PASSWORD)
    people = [("mohamed", mine), ("wolf", theirs)]
    for username, workspace in people:
        user = User(username=username, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))

    channels = {}
    for name, workspace in [("mine", mine), ("theirs", theirs)]:
        channel = Channel(workspace_id=workspace.id, kind="web", name="Web chat")
        migrated.add(channel)
        channels[name] = channel
    await migrated.flush()
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username, _ in people:
            http = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients, migrated, mine, theirs, channels, transport
        finally:
            for http in clients.values():
                await http.aclose()


async def _add(db: AsyncSession, workspace, channel, *, hours: float, handling=None) -> None:
    db.add(
        Conversation(
            workspace_id=workspace.id,
            channel_id=channel.id,
            direction="inbound",
            started_at=_ago(hours),
            handling=handling,
        )
    )
    await db.commit()


async def test_an_empty_workspace_reports_nothing_rather_than_failing(stage) -> None:
    clients, *_ = stage
    answer = await clients["mohamed"].get("/api/home")
    assert answer.status_code == 200

    body = answer.json()
    assert body["conversations"] == 0
    assert body["waiting"] == 0
    # Not zero: nothing was measured, so there is no measurement to report.
    assert body["by_agent"] is None


async def test_it_counts_only_what_started_inside_the_window(stage) -> None:
    clients, db, mine, _, channels, _transport = stage
    await _add(db, mine, channels["mine"], hours=1)
    await _add(db, mine, channels["mine"], hours=3)
    await _add(db, mine, channels["mine"], hours=30)

    since = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=8)).isoformat()
    body = (await clients["mohamed"].get("/api/home", params={"since": since})).json()
    assert body["conversations"] == 2


async def test_by_agent_is_null_until_something_records_who_took_it(stage) -> None:
    """Otherwise the screen says the agent handled none of them, which is a claim."""
    clients, db, mine, _, channels, _transport = stage
    await _add(db, mine, channels["mine"], hours=1)
    await _add(db, mine, channels["mine"], hours=2)

    assert (await clients["mohamed"].get("/api/home")).json()["by_agent"] is None

    await _add(db, mine, channels["mine"], hours=3, handling="ai")
    await _add(db, mine, channels["mine"], hours=4, handling="human")

    body = (await clients["mohamed"].get("/api/home")).json()
    # Now something is known, so a number is honest - and it counts only the rows that
    # say "ai", not every row once any row is recorded.
    assert body["by_agent"] == 1
    assert body["conversations"] == 4


async def test_waiting_counts_only_what_still_needs_a_person(stage) -> None:
    clients, db, mine, *_ = stage
    db.add_all(
        [
            Notification(
                workspace_id=mine.id,
                category="review",
                needs_decision=True,
                message_key="web_chat_started",
                params={"preview": "hello"},
            ),
            # Already dealt with.
            Notification(
                workspace_id=mine.id,
                category="review",
                needs_decision=True,
                message_key="web_chat_started",
                params={"preview": "older"},
                resolved_at=_ago(2),
            ),
            # A record of something that happened, which nobody has to decide.
            Notification(
                workspace_id=mine.id,
                category="system",
                needs_decision=False,
                message_key="web_chat_started",
                params={"preview": "noted"},
            ),
        ]
    )
    await db.commit()

    assert (await clients["mohamed"].get("/api/home")).json()["waiting"] == 1


async def test_waiting_ignores_the_window(stage) -> None:
    """Something that has waited since Tuesday is the most in need of a person."""
    clients, db, mine, *_ = stage
    db.add(
        Notification(
            workspace_id=mine.id,
            category="review",
            needs_decision=True,
            message_key="web_chat_started",
            params={"preview": "last week"},
        )
    )
    await db.commit()

    since = dt.datetime.now(dt.UTC).isoformat()
    assert (await clients["mohamed"].get("/api/home", params={"since": since})).json()[
        "waiting"
    ] == 1


async def test_one_workspace_never_counts_another(stage) -> None:
    """D-028: the scope is in the query, and this is what says so."""
    clients, db, mine, theirs, channels, _transport = stage
    await _add(db, mine, channels["mine"], hours=1)
    await _add(db, theirs, channels["theirs"], hours=1)
    await _add(db, theirs, channels["theirs"], hours=2)
    db.add(
        Notification(
            workspace_id=theirs.id,
            category="review",
            needs_decision=True,
            message_key="web_chat_started",
            params={"preview": "not yours"},
        )
    )
    await db.commit()

    assert (await clients["mohamed"].get("/api/home")).json() | {"since": None} == {
        "since": None,
        "conversations": 1,
        "by_agent": None,
        "waiting": 0,
    }
    assert (await clients["wolf"].get("/api/home")).json()["conversations"] == 2


@pytest.mark.parametrize(
    ("sent", "reason"),
    [
        ("1999-01-01T00:00:00Z", "a window wider than any day"),
        ("2999-01-01T00:00:00Z", "a clock set to next century"),
    ],
)
async def test_an_impossible_window_is_clamped_not_obeyed(stage, sent, reason) -> None:
    """An unbounded `since` would make this a full-table count wearing a day's name."""
    clients, db, mine, _, channels, _transport = stage
    await _add(db, mine, channels["mine"], hours=100)

    body = (await clients["mohamed"].get("/api/home", params={"since": sent})).json()
    since = dt.datetime.fromisoformat(body["since"])
    now = dt.datetime.now(dt.UTC)
    assert now - dt.timedelta(hours=37) < since <= now, reason
    # And the conversation from four days ago is outside every allowed window.
    assert body["conversations"] == 0


async def test_a_naive_instant_is_read_as_utc(stage) -> None:
    """Easier to call by hand, and unambiguous about the moment it means."""
    clients, db, mine, _, channels, _transport = stage
    await _add(db, mine, channels["mine"], hours=1)

    naive = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=6)).replace(tzinfo=None).isoformat()
    body = (await clients["mohamed"].get("/api/home", params={"since": naive})).json()
    assert body["conversations"] == 1


async def test_it_is_closed_to_anybody_without_a_session(stage) -> None:
    *_, transport = stage
    async with AsyncClient(transport=transport, base_url="http://localhost") as stranger:
        assert (await stranger.get("/api/home")).status_code == 401
