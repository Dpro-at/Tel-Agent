"""The first migration, and what it guarantees.

The schema under test is built by **running the migration**, not by
`Base.metadata.create_all()`. Creating tables from the models would test the models
against themselves and pass even if the migration were empty — and the migration is
what actually reaches an installation.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import Base
from api.models import (
    App,
    AppInstall,
    Call,
    Channel,
    Conversation,
    Membership,
    Message,
    Number,
    User,
    Workspace,
)

# Every table that holds workspace-scoped data — D-028, with no exceptions to remember.
#
# `users` is deliberately absent: a person exists across workspaces and reaches them
# through `memberships`. So is `apps`, the catalogue of what this installation knows
# about, and `alembic_version`.
WORKSPACE_SCOPED = {
    "app_installs",
    "calls",
    "channels",
    "conversations",
    "messages",
    "numbers",
}


async def _a_workspace(session: AsyncSession) -> Workspace:
    workspace = Workspace(name="Wagner & Partner")
    session.add(workspace)
    await session.flush()
    return workspace


async def _a_channel(session: AsyncSession, workspace: Workspace) -> Channel:
    channel = Channel(workspace_id=workspace.id, kind="web", name="Website chat")
    session.add(channel)
    await session.flush()
    return channel


# The two dialects reach the same feature by different mechanisms: FTS5 is a shadow
# table queried with MATCH, PostgreSQL is a GIN index queried with a tsquery. The tests
# below assert the *feature*, so the difference is confined to this one helper.
def _search(session: AsyncSession, term: str) -> tuple[str, dict[str, str]]:
    if session.bind.dialect.name == "postgresql":
        return (
            "SELECT text FROM messages "
            "WHERE to_tsvector('simple', text) @@ plainto_tsquery('simple', :q)",
            {"q": term},
        )
    return (
        "SELECT m.text FROM messages_fts f JOIN messages m ON m.id = f.rowid "
        "WHERE messages_fts MATCH :q",
        {"q": term},
    )


def _count_matches(session: AsyncSession, term: str) -> tuple[str, dict[str, str]]:
    if session.bind.dialect.name == "postgresql":
        return (
            "SELECT COUNT(*) FROM messages "
            "WHERE to_tsvector('simple', text) @@ plainto_tsquery('simple', :q)",
            {"q": term},
        )
    return (
        "SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH :q",
        {"q": term},
    )


# --- Decision 6: conversations is the core table -----------------------------


async def test_a_conversation_is_stored_and_read_back(migrated: AsyncSession) -> None:
    """C1's acceptance condition, exactly as written."""
    workspace = await _a_workspace(migrated)
    channel = await _a_channel(migrated, workspace)

    conversation = Conversation(
        workspace_id=workspace.id,
        channel_id=channel.id,
        direction="inbound",
        summary="Asked about opening hours.",
    )
    migrated.add(conversation)
    await migrated.flush()

    migrated.add_all(
        [
            Message(
                workspace_id=workspace.id,
                conversation_id=conversation.id,
                ts_ms=0,
                speaker="caller",
                text="Hallo",
            ),
            Message(
                workspace_id=workspace.id,
                conversation_id=conversation.id,
                ts_ms=1200,
                speaker="agent",
                text="Guten Tag",
            ),
        ]
    )
    await migrated.commit()

    stored = await migrated.get(Conversation, conversation.id)
    assert stored is not None
    assert stored.status == "open"
    assert stored.workspace_id == workspace.id

    rows = (
        await migrated.execute(
            text("SELECT text FROM messages WHERE conversation_id = :id ORDER BY ts_ms"),
            {"id": conversation.id},
        )
    ).scalars()
    assert list(rows) == ["Hallo", "Guten Tag"]


async def test_a_web_conversation_has_no_calls_row(migrated: AsyncSession) -> None:
    """A conversation is not a call. `calls` is the phone-only extension."""
    workspace = await _a_workspace(migrated)
    channel = await _a_channel(migrated, workspace)
    conversation = Conversation(
        workspace_id=workspace.id, channel_id=channel.id, direction="inbound"
    )
    migrated.add(conversation)
    await migrated.commit()

    # Queried rather than reached through `conversation.call`: touching an unloaded
    # relationship after commit is implicit IO, which an async session refuses - and
    # in a request handler that refusal is a 500, so tests should not rely on it.
    calls = (
        await migrated.execute(
            text("SELECT COUNT(*) FROM calls WHERE conversation_id = :id"),
            {"id": conversation.id},
        )
    ).scalar_one()
    assert calls == 0


# --- Decision 1 / D-028: workspace_id everywhere -----------------------------


async def test_every_data_table_carries_workspace_id(migrated: AsyncSession) -> None:
    """The isolation key is on every table that holds data (D-028)."""
    connection = await migrated.connection()
    tables = await connection.run_sync(
        lambda sync: {name: inspect(sync).get_columns(name) for name in WORKSPACE_SCOPED}
    )

    for name, columns in tables.items():
        assert "workspace_id" in {column["name"] for column in columns}, name


async def test_deleting_a_workspace_takes_its_conversations(migrated: AsyncSession) -> None:
    """Transcripts are personal data. A row nothing points at is a row nobody deletes."""
    workspace = await _a_workspace(migrated)
    channel = await _a_channel(migrated, workspace)
    migrated.add(
        Conversation(workspace_id=workspace.id, channel_id=channel.id, direction="inbound")
    )
    await migrated.commit()

    await migrated.execute(
        text("DELETE FROM conversations WHERE workspace_id = :id"), {"id": workspace.id}
    )
    await migrated.execute(
        text("DELETE FROM channels WHERE workspace_id = :id"), {"id": workspace.id}
    )
    await migrated.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": workspace.id})
    await migrated.commit()

    remaining = (
        await migrated.execute(text("SELECT COUNT(*) FROM conversations"))
    ).scalar_one()
    assert remaining == 0


async def test_a_person_can_hold_a_different_role_in_each_workspace(
    migrated: AsyncSession,
) -> None:
    """ "Shared with you by Sabine" in the workspace switcher is this row."""
    user = User(username="lukas")
    first = Workspace(name="Wagner & Partner")
    second = Workspace(name="Wolf Studio")
    migrated.add_all([user, first, second])
    await migrated.flush()

    migrated.add_all(
        [
            Membership(user_id=user.id, workspace_id=first.id, role="owner"),
            Membership(user_id=user.id, workspace_id=second.id, role="viewer"),
        ]
    )
    await migrated.commit()

    roles = (
        await migrated.execute(
            text("SELECT role FROM memberships WHERE user_id = :id ORDER BY workspace_id"),
            {"id": user.id},
        )
    ).scalars()
    assert list(roles) == ["owner", "viewer"]


async def test_one_membership_per_person_per_workspace(migrated: AsyncSession) -> None:
    """Two rows with different roles would make "what may they do here" ambiguous."""
    user = User(username="sabine")
    workspace = Workspace(name="Wagner & Partner")
    migrated.add_all([user, workspace])
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
    await migrated.commit()

    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="viewer"))
    with pytest.raises(IntegrityError):
        await migrated.commit()


async def test_an_unknown_role_is_refused_by_the_database(migrated: AsyncSession) -> None:
    """The CHECK constraint the migration created, not SQLAlchemy's own validation.

    Inserted with raw SQL on purpose: the ORM would reject `superuser` before it ever
    reached the database, and this needs to prove the *schema* refuses it. A native
    enum type would behave differently on the two dialects, which is why enumerated
    columns are text plus CHECK (D-029).
    """
    user = User(username="mallory")
    workspace = Workspace(name="Wagner & Partner")
    migrated.add_all([user, workspace])
    await migrated.commit()

    with pytest.raises(IntegrityError):
        await migrated.execute(
            text(
                "INSERT INTO memberships (user_id, workspace_id, role) "
                "VALUES (:u, :w, 'superuser')"
            ),
            {"u": user.id, "w": workspace.id},
        )
        await migrated.commit()


# --- Decisions 3, 4 and 5 ----------------------------------------------------


async def test_a_number_records_who_holds_it(migrated: AsyncSession) -> None:
    """Decision 3. Backfilling this once both kinds exist means guessing."""
    workspace = await _a_workspace(migrated)
    migrated.add(
        Number(
            workspace_id=workspace.id,
            provider="twilio",
            owner="customer",
            e164="+4315551234",
        )
    )
    await migrated.commit()

    owner = (await migrated.execute(text("SELECT owner FROM numbers"))).scalar_one()
    assert owner == "customer"


async def test_call_cost_is_stored_as_integer_micros(migrated: AsyncSession) -> None:
    """Decision 4. Money in binary floating point does not add up."""
    workspace = await _a_workspace(migrated)
    channel = Channel(workspace_id=workspace.id, kind="phone", name="Main line")
    migrated.add(channel)
    await migrated.flush()
    conversation = Conversation(
        workspace_id=workspace.id, channel_id=channel.id, direction="inbound"
    )
    migrated.add(conversation)
    await migrated.flush()

    migrated.add(
        Call(
            conversation_id=conversation.id,
            workspace_id=workspace.id,
            from_e164="+4319998888",
            billable_seconds=137,
            provider_cost_micros=1_234_567,
        )
    )
    await migrated.commit()

    stored = await migrated.get(Call, conversation.id)
    assert stored is not None
    assert isinstance(stored.provider_cost_micros, int)
    assert stored.provider_cost_micros == 1_234_567


async def test_a_typed_line_has_no_confidence_and_a_spoken_one_does(
    migrated: AsyncSession,
) -> None:
    """Decision 5, per line.

    Null on a text channel is not missing data — it is the signal that the line was
    typed rather than spoken.
    """
    workspace = await _a_workspace(migrated)
    channel = await _a_channel(migrated, workspace)
    conversation = Conversation(
        workspace_id=workspace.id, channel_id=channel.id, direction="inbound"
    )
    migrated.add(conversation)
    await migrated.flush()

    typed = Message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        ts_ms=0,
        speaker="caller",
        text="Hallo",
    )
    spoken = Message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        ts_ms=900,
        speaker="caller",
        text="Grüß Gott",
        stt_confidence=0.62,
        language="de",
    )
    migrated.add_all([typed, spoken])
    await migrated.commit()

    # The query Rule 4 asks for: show every line under 0.7.
    weak = (
        await migrated.execute(text("SELECT text FROM messages WHERE stt_confidence < 0.7"))
    ).scalars()
    assert list(weak) == ["Grüß Gott"]
    assert typed.stt_confidence is None
    assert typed.language is None


# --- Decision 2: full-text search --------------------------------------------


async def test_messages_are_searchable_by_phrase(migrated: AsyncSession) -> None:
    """Decision 2, and §A6.3's headline feature.

    An index added later means the data was all there and the feature was not — so it
    is built in the first migration and proved here.
    """
    workspace = await _a_workspace(migrated)
    channel = await _a_channel(migrated, workspace)
    conversation = Conversation(
        workspace_id=workspace.id, channel_id=channel.id, direction="inbound"
    )
    migrated.add(conversation)
    await migrated.flush()

    migrated.add_all(
        [
            Message(
                workspace_id=workspace.id,
                conversation_id=conversation.id,
                ts_ms=0,
                speaker="caller",
                text="Ich brauche einen Termin fuer die Zahnreinigung",
            ),
            Message(
                workspace_id=workspace.id,
                conversation_id=conversation.id,
                ts_ms=1000,
                speaker="agent",
                text="Gerne, wann passt es Ihnen",
            ),
        ]
    )
    await migrated.commit()

    sql, params = _search(migrated, "Zahnreinigung")
    hits = (await migrated.execute(text(sql), params)).scalars()
    assert list(hits) == ["Ich brauche einen Termin fuer die Zahnreinigung"]


async def test_the_search_index_follows_edits_and_deletes(migrated: AsyncSession) -> None:
    """The index must follow the table, on both dialects.

    On PostgreSQL an expression index is maintained by the database and this is close to
    free. On SQLite an external-content FTS5 table is not maintained at all without the
    three triggers the migration installs - the index silently stops matching rows
    written after it was created, and search returns fewer results over time with
    nothing reporting an error. Same assertion, two very different things being proved.
    """
    workspace = await _a_workspace(migrated)
    channel = await _a_channel(migrated, workspace)
    conversation = Conversation(
        workspace_id=workspace.id, channel_id=channel.id, direction="inbound"
    )
    migrated.add(conversation)
    await migrated.flush()
    message = Message(
        workspace_id=workspace.id,
        conversation_id=conversation.id,
        ts_ms=0,
        speaker="caller",
        text="Rechnung offen",
    )
    migrated.add(message)
    await migrated.commit()

    async def matches(term: str) -> int:
        sql, params = _count_matches(migrated, term)
        return (await migrated.execute(text(sql), params)).scalar_one()

    assert await matches("Rechnung") == 1

    await migrated.execute(
        text("UPDATE messages SET text = :t WHERE id = :id"),
        {"t": "Mahnung offen", "id": message.id},
    )
    await migrated.commit()
    assert await matches("Rechnung") == 0
    assert await matches("Mahnung") == 1

    await migrated.execute(text("DELETE FROM messages WHERE id = :id"), {"id": message.id})
    await migrated.commit()
    assert await matches("Mahnung") == 0


# --- The extension registry (D-031) ------------------------------------------


async def test_an_app_is_installed_per_workspace(migrated: AsyncSession) -> None:
    """Two workspaces on one machine run different sets of apps."""
    first = Workspace(name="Wagner & Partner")
    second = Workspace(name="Wolf Studio")
    app = App(slug="web_chat", origin="official", version="1.6.3")
    migrated.add_all([first, second, app])
    await migrated.flush()

    migrated.add(AppInstall(workspace_id=first.id, app_id=app.id, enabled=True))
    migrated.add(AppInstall(workspace_id=second.id, app_id=app.id, enabled=False))
    await migrated.commit()

    states = (
        await migrated.execute(text("SELECT enabled FROM app_installs ORDER BY workspace_id"))
    ).scalars()
    assert [bool(state) for state in states] == [True, False]


async def test_an_app_cannot_be_installed_twice_in_one_workspace(
    migrated: AsyncSession,
) -> None:
    workspace = await _a_workspace(migrated)
    app = App(slug="telegram", origin="community")
    migrated.add(app)
    await migrated.flush()
    migrated.add(AppInstall(workspace_id=workspace.id, app_id=app.id))
    await migrated.commit()

    migrated.add(AppInstall(workspace_id=workspace.id, app_id=app.id))
    with pytest.raises(IntegrityError):
        await migrated.commit()


# --- The migration matches the models ----------------------------------------


async def test_the_migration_produces_every_table_the_models_declare(
    migrated: AsyncSession,
) -> None:
    """A model whose table the migration never creates fails at runtime, not here."""
    connection = await migrated.connection()
    present = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))

    assert set(Base.metadata.tables) <= present


async def test_timestamps_are_filled_in_by_the_database(migrated: AsyncSession) -> None:
    """`server_default`, so a row written by a seed script gets the same treatment."""
    workspace = await _a_workspace(migrated)
    await migrated.commit()
    await migrated.refresh(workspace)

    assert isinstance(workspace.created_at, dt.datetime)
