"""Taking a conversation over — §A6.7's second intervention.

The whisper coaches the agent; a takeover replaces it. Three moves, and each has its
own rule:

- **Take over** puts `handling` on `human`, and from that moment the agent is silent —
  the widget's reply stream produces nothing and stores nothing. A takeover where the
  agent keeps talking is two voices answering one customer.
- **Reply** is a person typing as the business. It exists only while the thread is
  taken over: outside that mode the box a person would type into is the whisper, and a
  reply that lands beside an agent's own answer is the two-voices problem again.
- **Resume** hands the thread back, and the agent answers the next message as before.

The visitor-side halves already exist: the widget's thread endpoint shows a `human`
line as the business (`_VISIBLE_SPEAKERS`), and the model is handed one as its own
side of the conversation (`_ROLES`). These tests are the dashboard's half.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


async def _thread(
    db: AsyncSession,
    workspace: Workspace,
    channel: Channel,
    *,
    status: str = "open",
    handling: str = "ai",
) -> Conversation:
    row = Conversation(
        workspace_id=workspace.id,
        channel_id=channel.id,
        direction="inbound",
        status=status,
        handling=handling,
    )
    db.add(row)
    await db.flush()
    db.add(
        Message(
            workspace_id=workspace.id,
            conversation_id=row.id,
            ts_ms=1000,
            speaker="caller",
            text="I would rather talk to a person, please.",
        )
    )
    return row


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces with a live thread each, and the roles that may or may not act."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    web = Channel(workspace_id=mine.id, kind="web", name="Website")
    other = Channel(workspace_id=theirs.id, kind="web", name="Website")
    migrated.add_all([web, other])
    await migrated.flush()

    threads = {
        "live": await _thread(migrated, mine, web),
        "taken": await _thread(migrated, mine, web, handling="human"),
        "ended": await _thread(migrated, mine, web, status="closed"),
        "theirs": await _thread(migrated, theirs, other),
    }
    ids = {name: row.id for name, row in threads.items()}

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
            yield clients, ids, migrated
        finally:
            for http in clients.values():
                await http.aclose()


async def test_taking_over_puts_the_thread_in_human_hands(stage) -> None:
    clients, ids, db = stage
    answer = await clients["sabine"].post(f"/api/conversations/{ids['live']}/takeover")
    assert answer.status_code == 200, answer.text
    assert answer.json()["handling"] == "human"

    db.expire_all()
    row = await db.scalar(select(Conversation).where(Conversation.id == ids["live"]))
    assert row.handling == "human"


async def test_resuming_hands_it_back_to_the_agent(stage) -> None:
    clients, ids, db = stage
    answer = await clients["sabine"].post(f"/api/conversations/{ids['taken']}/resume")
    assert answer.status_code == 200, answer.text
    assert answer.json()["handling"] == "ai"

    db.expire_all()
    row = await db.scalar(select(Conversation).where(Conversation.id == ids["taken"]))
    assert row.handling == "ai"


async def test_a_reply_is_a_person_speaking_as_the_business(stage) -> None:
    clients, ids, db = stage
    sent = await clients["sabine"].post(
        f"/api/conversations/{ids['taken']}/reply",
        json={"text": "Thursday at ten works. I have booked it."},
    )
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["speaker"] == "human"
    assert body["is_whisper"] is False
    assert body["author"] == "sabine"

    row = await db.scalar(select(Message).where(Message.id == body["id"]))
    assert row is not None
    assert row.is_whisper is False
    author = await db.scalar(select(User).where(User.id == row.author_user_id))
    assert author.username == "sabine"
    # On a text channel both are null, and that null is itself the signal that the
    # line was typed rather than spoken (§B5 decision 5).
    assert row.stt_confidence is None
    assert row.language is None


async def test_a_reply_needs_the_thread_to_be_taken_over_first(stage) -> None:
    """Outside the takeover the box a person types into is the whisper.

    A reply landing beside the agent's own answer would be two voices answering one
    customer — the exact thing the `handling` flag exists to prevent.
    """
    clients, ids, _ = stage
    refused = await clients["sabine"].post(
        f"/api/conversations/{ids['live']}/reply", json={"text": "Hello, it is me."}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "not_taken_over"


async def test_a_viewer_may_read_and_never_intervene(stage) -> None:
    clients, ids, _ = stage
    for action in ("takeover", "resume", "reply"):
        refused = await clients["lukas"].post(
            f"/api/conversations/{ids['taken']}/{action}",
            json={"text": "Say yes."} if action == "reply" else None,
        )
        assert refused.status_code == 403, action


async def test_another_workspaces_thread_answers_like_a_missing_one(stage) -> None:
    clients, ids, _ = stage
    for action in ("takeover", "resume", "reply"):
        refused = await clients["sabine"].post(
            f"/api/conversations/{ids['theirs']}/{action}",
            json={"text": "Hello."} if action == "reply" else None,
        )
        assert refused.status_code == 404, action


async def test_a_thread_that_has_ended_refuses_every_intervention(stage) -> None:
    """Nothing is listening, and nobody is on the other end to be spoken to."""
    clients, ids, _ = stage
    for action in ("takeover", "resume", "reply"):
        refused = await clients["sabine"].post(
            f"/api/conversations/{ids['ended']}/{action}",
            json={"text": "One more thing."} if action == "reply" else None,
        )
        assert refused.status_code == 409, action
        assert refused.json()["error"]["code"] == "conversation_closed", action


async def test_an_empty_reply_is_refused(stage) -> None:
    clients, ids, _ = stage
    refused = await clients["sabine"].post(
        f"/api/conversations/{ids['taken']}/reply", json={"text": "   "}
    )
    assert refused.status_code == 422


async def test_taking_over_twice_is_not_an_error(stage) -> None:
    """Two colleagues pressing the button is one takeover, not a conflict.

    The second press changes nothing and says so by answering the same way — the
    screen polls every few seconds, and a refusal here would surface as an error to
    somebody who merely lost the race.
    """
    clients, ids, _ = stage
    first = await clients["sabine"].post(f"/api/conversations/{ids['taken']}/takeover")
    assert first.status_code == 200
    assert first.json()["handling"] == "human"
