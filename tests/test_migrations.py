"""What every migration has to be true of, checked without a database.

D-029 says one schema serves PostgreSQL and SQLite. The suite already proves that by
running against both, and that is the real check — but only on CI, where a PostgreSQL
container exists. On a developer's machine the PostgreSQL half is skipped, so a
migration that cannot run there passes locally, gets pushed, and fails ten minutes
later in a job that had to build a container first.

These are the cheap checks that catch the difference before the push. They read the
migration files as text on purpose: a migration is a frozen artifact with its own
literals, and the model it was generated from being correct says nothing about it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import CHANNEL_KINDS, Channel, Workspace

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# `sa.Column("x", sa.Boolean(), server_default=sa.text("1"))` - the exact shape
# autogenerate produces from a model whose default was written as a SQLite literal.
BOOLEAN_INTEGER_DEFAULT = re.compile(
    r"""sa\.Boolean\(\)\s*,\s*server_default\s*=\s*sa\.text\(\s*["']\s*[01]\s*["']\s*\)""",
    re.VERBOSE,
)

# The kinds this wave added that are longer than the nine characters the column was
# created with. They are the reason the migration alters the column and not only the
# constraint.
_LONG_KINDS = ("google_chat", "mattermost")


def _migrations() -> list[Path]:
    files = sorted(path for path in VERSIONS.glob("*.py") if path.name != "__init__.py")
    assert files, "no migrations found - this test is checking nothing"
    return files


@pytest.mark.parametrize("path", _migrations(), ids=lambda path: path.stem)
def test_a_boolean_default_is_written_for_both_dialects(path: Path) -> None:
    """`1` is a boolean in SQLite and an integer in PostgreSQL.

    PostgreSQL refuses the whole CREATE TABLE with "column is of type boolean but
    default expression is of type integer", so the migration does not half-apply -
    it does not apply at all, and every test that needs the table errors at setup.

    `sa.true()` and `sa.false()` render per dialect, which is what makes one migration
    serve both. This cost a CI run once; it costs a regex now.
    """
    source = path.read_text(encoding="utf-8")
    found = BOOLEAN_INTEGER_DEFAULT.findall(source)
    assert not found, (
        f"{path.name} gives a boolean column an integer default: {found}. "
        "Use sa.true() or sa.false(), which render per dialect."
    )


@pytest.mark.parametrize("path", _migrations(), ids=lambda path: path.stem)
def test_a_migration_can_be_undone(path: Path) -> None:
    """A `downgrade` that does nothing is a migration nobody can back out of.

    Not run - read. Running it would need a database per migration, and the failure
    this catches is the one where autogenerate produced an empty body and it was
    committed with the `pass` still in it.
    """
    source = path.read_text(encoding="utf-8")
    body = source.split("def downgrade()", 1)
    assert len(body) == 2, f"{path.name} has no downgrade at all"

    statements = [
        line.strip()
        for line in body[1].splitlines()
        if line.strip() and not line.strip().startswith("#") and '"""' not in line
    ]
    assert statements, f"{path.name} has an empty downgrade"
    assert statements != ["pass"], (
        f"{path.name} has a downgrade that does nothing. If it genuinely cannot be "
        "undone, say so in a comment above the `pass` so the next reader knows it was "
        "a decision rather than a leftover."
    )


@pytest.mark.parametrize("path", _migrations(), ids=lambda path: path.stem)
def test_a_batch_alter_of_messages_puts_the_search_triggers_back(path: Path) -> None:
    """`batch_alter_table("messages")` silently unindexes the transcript archive.

    SQLite cannot alter a table in place for most operations, so alembic's batch mode
    rebuilds it: new table, copy, drop, rename. The three FTS5 triggers created in
    `56268f297c2b` are attached to the table that gets dropped, so they go with it —
    and nothing errors. Full-text search keeps answering, from an index that stops
    being updated. The bug surfaces as "older conversations are findable and newer ones
    are not", months later, with no failure to point at.

    Caught once, by four tests that had nothing to do with the migration that broke
    them. This is the cheap version of that.
    """
    source = path.read_text(encoding="utf-8")
    rebuilds = any(
        f"batch_alter_table({quote}messages{quote}" in source for quote in ('"', "'")
    )
    if not rebuilds:
        pytest.skip("does not rebuild `messages`")

    assert "messages_fts" in source, (
        f"{path.name} rebuilds `messages` without restoring `messages_fts`. On SQLite "
        "that drops the search triggers and leaves the index frozen. Drop them before "
        "the batch, recreate them after, and rebuild — see afa4aef2e4c9."
    )


# --- What the migrated schema has to accept ----------------------------------------


async def test_a_channel_of_a_long_named_kind_can_actually_be_written(
    migrated: AsyncSession,
) -> None:
    """The CHECK constraint is only half of what `kind` is.

    `enum_column` renders a non-native enum, which is a VARCHAR sized to the longest
    value it was created with - nine, from `instagram`. PostgreSQL enforces that width,
    so widening the constraint alone leaves `google_chat` (eleven) and `mattermost`
    (ten) rejected by the column itself. SQLite does not enforce it, which is exactly
    why the width is asserted here as well as the insert: it is the half of this that
    a developer's machine cannot fail on its own.
    """
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()

    for kind in ("google_chat", "mattermost"):
        migrated.add(
            Channel(workspace_id=workspace.id, kind=kind, name=kind, status="disabled")
        )
    await migrated.commit()

    stored = sorted(
        await migrated.scalars(select(Channel.kind).where(Channel.kind.in_(_LONG_KINDS)))
    )
    assert stored == sorted(_LONG_KINDS)


async def test_the_kind_column_is_wide_enough_for_every_kind(migrated: AsyncSession) -> None:
    def read(connection: object) -> int | None:
        columns = inspect(connection).get_columns("channels")
        kind = next(column for column in columns if column["name"] == "kind")
        return getattr(kind["type"], "length", None)

    width = await migrated.run_sync(lambda session: read(session.connection()))
    assert width is not None, "the kind column has no declared width to check"
    assert width >= max(len(kind) for kind in CHANNEL_KINDS)
