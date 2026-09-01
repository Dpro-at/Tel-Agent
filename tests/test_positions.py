"""`messages.ts_ms` is a position in the conversation, not a clock.

The column has said so since the first migration: *"Milliseconds since the conversation
started, not a wall clock. On a call the position within the recording is what a
transcript is read against, and a clock adjustment mid-call must not be able to reorder
the lines."* Two screens read it that way — the archive adds it to `started_at`, and the
call detail renders it as `mm:ss` into the recording.

Every writer wrote epoch milliseconds instead, and nothing failed: ordering is unaffected
because every row on a thread used the same clock. Only a rendered timestamp showed it,
and it showed it as the year 2083 in the archive and `29803424:52` on a call.

These tests are about the writers agreeing with the column, and about the rows already
stored the other way.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105

# Anything at or above this is a wall clock, not a position: it is about fifty-six
# years of milliseconds, and no conversation runs that long.
EPOCH_SCALE = 10**12


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """A workspace with an enabled web channel, and somebody who may whisper."""
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()

    channel = Channel(
        workspace_id=workspace.id,
        kind="web",
        name="Website",
        status="active",
        settings_json={"allowed_origins": ["http://localhost"]},
        webhook_path="a-public-path",
    )
    migrated.add(channel)
    await migrated.flush()

    user = User(username="sabine", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="reception"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        signed_in = AsyncClient(transport=transport, base_url="http://localhost")
        assert (
            await signed_in.post(
                "/api/auth/login", json={"username": "sabine", "password": PASSWORD}
            )
        ).status_code == 200
        anonymous = AsyncClient(transport=transport, base_url="http://localhost")
        try:
            yield signed_in, anonymous, channel, migrated
        finally:
            await signed_in.aclose()
            await anonymous.aclose()


async def test_a_visitors_line_is_a_position_not_a_clock(stage) -> None:
    _, anonymous, channel, db = stage
    sent = await anonymous.post(
        f"/public/chat/{channel.webhook_path}/messages",
        json={"text": "Is the quote still good?"},
        headers={"Origin": "http://localhost"},
    )
    assert sent.status_code == 201, sent.text

    row = await db.scalar(select(Message).where(Message.id == sent.json()["message_id"]))
    assert row.ts_ms < EPOCH_SCALE, f"{row.ts_ms} is a wall clock, not a position"
    # The first line of a thread sits at its start, give or take the write itself.
    assert row.ts_ms < 5_000


async def test_a_whisper_is_positioned_the_same_way(stage) -> None:
    """Three writers, one meaning. A whisper written against a different clock would
    sort to one end of the transcript instead of where it was said."""
    signed_in, anonymous, channel, db = stage
    await anonymous.post(
        f"/public/chat/{channel.webhook_path}/messages",
        json={"text": "Hello."},
        headers={"Origin": "http://localhost"},
    )
    thread = await db.scalar(select(Conversation))

    whispered = await signed_in.post(
        f"/api/conversations/{thread.id}/whisper", json={"text": "Offer Thursday."}
    )
    assert whispered.status_code == 201, whispered.text
    assert whispered.json()["ts_ms"] < EPOCH_SCALE


async def test_a_later_line_sits_after_an_earlier_one(stage) -> None:
    """The property the column exists for, and the one a backfill must not break."""
    _, anonymous, channel, db = stage
    first = await anonymous.post(
        f"/public/chat/{channel.webhook_path}/messages",
        json={"text": "First."},
        headers={"Origin": "http://localhost"},
    )
    handle = first.json()["conversation"]
    second = await anonymous.post(
        f"/public/chat/{channel.webhook_path}/messages",
        json={"text": "Second.", "conversation": handle},
        headers={"Origin": "http://localhost"},
    )

    rows = {row.id: row.ts_ms for row in (await db.execute(select(Message))).scalars().all()}
    assert rows[second.json()["message_id"]] >= rows[first.json()["message_id"]]


async def test_a_position_is_never_negative(stage) -> None:
    """A line written a hair before `started_at` settles is at the start, not before it.

    `started_at` is a server default, so the row's clock and the process's clock are not
    the same clock. Without a floor, the first line of every conversation could carry a
    negative position — and `mm:ss` of a negative number is not a thing anybody should
    have to read.
    """
    _, anonymous, channel, db = stage
    await anonymous.post(
        f"/public/chat/{channel.webhook_path}/messages",
        json={"text": "Hello."},
        headers={"Origin": "http://localhost"},
    )
    rows = (await db.execute(select(Message))).scalars().all()
    assert all(row.ts_ms >= 0 for row in rows)


def test_the_helper_measures_from_the_start() -> None:
    from api.conversations import position_ms

    started = dt.datetime(2026, 8, 31, 14, 0, tzinfo=dt.UTC)
    assert position_ms(started, at=started) == 0
    assert position_ms(started, at=started + dt.timedelta(seconds=7)) == 7_000
    # Naive in, aware out: SQLite hands back naive datetimes for a column written aware.
    assert position_ms(started.replace(tzinfo=None), at=started + dt.timedelta(minutes=1))
    # A clock that went backwards does not produce a line before the conversation.
    assert position_ms(started, at=started - dt.timedelta(seconds=30)) == 0


async def test_the_backfill_turns_stored_clocks_into_positions(
    settings: Settings, database_url: str
) -> None:
    """The rows written before the writers were fixed.

    Built by upgrading to the revision *before* the backfill, writing rows the way the
    old code wrote them, and then running it. Asserting on a hand-called function would
    prove the arithmetic and not the migration, and the migration is what reaches an
    installation.
    """
    import asyncio
    import os

    from alembic.config import Config
    from sqlalchemy import text as sa_text

    from alembic import command
    from api.config import get_settings
    from api.db import create_engine
    from tests.conftest import REPO_ROOT, _reset_postgres

    if database_url.startswith("postgresql"):
        await _reset_postgres(database_url)

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()

    # The schema as it stood before this change.
    await asyncio.to_thread(command.upgrade, config, "afa4aef2e4c9")

    engine = create_engine(Settings(_env_file=None, database_url=database_url))
    started = dt.datetime(2026, 8, 31, 14, 0, tzinfo=dt.UTC)
    started_ms = int(started.timestamp() * 1000)

    async with engine.begin() as connection:
        await connection.execute(
            sa_text("INSERT INTO workspaces (id, name) VALUES (1, 'Wagner & Partner')")
        )
        await connection.execute(
            sa_text(
                "INSERT INTO channels (id, workspace_id, kind, name, status)"
                " VALUES (1, 1, 'web', 'Web', 'active')"
            )
        )
        await connection.execute(
            sa_text(
                "INSERT INTO conversations (id, workspace_id, channel_id, direction, status,"
                " started_at) VALUES (1, 1, 1, 'inbound', 'open', :started)"
            ),
            {"started": started},
        )
        # Two lines as the old writers stored them, seven and nineteen seconds in, plus
        # one already written the new way — which the backfill must leave alone.
        for row_id, ts_ms in ((1, started_ms + 7_000), (2, started_ms + 19_000), (3, 4_000)):
            await connection.execute(
                sa_text(
                    "INSERT INTO messages (id, workspace_id, conversation_id, ts_ms, speaker,"
                    " text, is_whisper) VALUES (:id, 1, 1, :ts, 'caller', 'hello', :whisper)"
                ),
                {"id": row_id, "ts": ts_ms, "whisper": False},
            )

    await asyncio.to_thread(command.upgrade, config, "head")

    async with engine.connect() as connection:
        result = await connection.execute(sa_text("SELECT id, ts_ms FROM messages ORDER BY id"))
        rows = dict(result.all())
    await engine.dispose()

    assert rows[1] == 7_000, "a stored clock becomes the position it always meant"
    assert rows[2] == 19_000
    assert rows[3] == 4_000, "a value already a position is left alone"
    # The property the column exists for, and the one a backfill could break.
    assert rows[1] < rows[2]
