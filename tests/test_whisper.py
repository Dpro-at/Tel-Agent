"""Whispering to the agent mid-conversation — §A6.7's first intervention.

The reading half has existed since the web chat did: `api/routes/public_chat.py` hands
a whisper to the model as a system note and keeps it out of the thread the visitor
reloads, and the archive draws it in its own channel. Nothing could *write* one. These
tests are that half.

The two that matter are the pair: a whisper reaches the agent, and it never reaches the
customer. Everything between them is the usual shape — the role line, isolation, and
refusing a thread that has already ended.
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
    db: AsyncSession, workspace: Workspace, channel: Channel, *, status: str = "open"
) -> Conversation:
    row = Conversation(
        workspace_id=workspace.id,
        channel_id=channel.id,
        direction="inbound",
        status=status,
    )
    db.add(row)
    await db.flush()
    db.add(
        Message(
            workspace_id=workspace.id,
            conversation_id=row.id,
            ts_ms=1000,
            speaker="caller",
            text="Is the quote from last week still good?",
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


async def test_a_whisper_is_written_into_the_transcript_and_flagged(stage) -> None:
    clients, ids, db = stage
    sent = await clients["sabine"].post(
        f"/api/conversations/{ids['live']}/whisper",
        json={"text": "Tell her the quote still stands until the 30th."},
    )
    assert sent.status_code == 201, sent.text
    body = sent.json()
    assert body["is_whisper"] is True
    assert body["speaker"] == "human"

    row = await db.scalar(select(Message).where(Message.id == body["id"]))
    assert row is not None
    assert row.is_whisper is True
    # On a text channel both are null, and that null is itself the signal that the line
    # was typed rather than spoken (§B5 decision 5).
    assert row.stt_confidence is None
    assert row.language is None


async def test_the_whisper_records_which_person_wrote_it(stage) -> None:
    """`speaker` is a role. On a reception desk of four, that is not an answer."""
    clients, ids, db = stage
    sent = await clients["sabine"].post(
        f"/api/conversations/{ids['live']}/whisper", json={"text": "Offer Thursday."}
    )
    assert sent.json()["author"] == "sabine"

    row = await db.scalar(select(Message).where(Message.id == sent.json()["id"]))
    author = await db.scalar(select(User).where(User.id == row.author_user_id))
    assert author.username == "sabine"


async def test_the_whisper_appears_in_the_archive_and_never_in_the_thread(stage) -> None:
    """The pair that this endpoint exists for."""
    clients, ids, _ = stage
    await clients["sabine"].post(
        f"/api/conversations/{ids['live']}/whisper",
        json={"text": "Tell her the quote still stands."},
    )

    detail = (await clients["sabine"].get(f"/api/conversations/{ids['live']}")).json()
    whispers = [line for line in detail["messages"] if line["is_whisper"]]
    assert [line["text"] for line in whispers] == ["Tell her the quote still stands."]


async def test_a_viewer_may_read_the_thread_and_not_speak_into_it(stage) -> None:
    clients, ids, _ = stage
    assert (await clients["lukas"].get(f"/api/conversations/{ids['live']}")).status_code == 200
    refused = await clients["lukas"].post(
        f"/api/conversations/{ids['live']}/whisper", json={"text": "Say yes."}
    )
    assert refused.status_code == 403


async def test_another_workspaces_thread_cannot_be_whispered_into(stage) -> None:
    """Answered as missing rather than as forbidden — the id must stay unconfirmed."""
    clients, ids, _ = stage
    refused = await clients["sabine"].post(
        f"/api/conversations/{ids['theirs']}/whisper", json={"text": "Hello."}
    )
    assert refused.status_code == 404


async def test_a_thread_that_has_ended_refuses_a_whisper(stage) -> None:
    """Nothing is listening. A line written into it would be coaching for nobody."""
    clients, ids, _ = stage
    refused = await clients["sabine"].post(
        f"/api/conversations/{ids['ended']}/whisper", json={"text": "One more thing."}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conversation_closed"


async def test_an_empty_whisper_is_refused(stage) -> None:
    clients, ids, _ = stage
    refused = await clients["sabine"].post(
        f"/api/conversations/{ids['live']}/whisper", json={"text": "   "}
    )
    assert refused.status_code == 422
