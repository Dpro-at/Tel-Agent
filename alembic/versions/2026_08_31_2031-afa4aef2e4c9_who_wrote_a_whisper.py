"""who wrote a whisper

`messages.speaker` is a role — `caller`, `agent`, `human`. On a reception desk of four,
`human` does not answer who coached the agent into what it then told a customer, and
§A6.4's own transcript names the person. This is the column that name comes from.

Null on every line a person did not write, which is nearly all of them. `SET NULL`
rather than `CASCADE`: an account can be removed from an installation, and deleting the
lines they wrote would take a customer's conversation with it.

**Adding a column to `messages` is not a small migration on SQLite, and this is the
trap.** SQLite cannot add a foreign key to an existing table, so `batch_alter_table`
rebuilds it: new table, copy, drop, rename. The rebuild takes the three FTS5 triggers
with it, because a trigger belongs to the table it is attached to and the dropped table
was that table. Nothing errors. Search simply stops seeing anything written afterwards,
and the failure looks like "the index is out of date" months later.

So the triggers are dropped deliberately, recreated verbatim from `56268f297c2b`, and
the shadow table is rebuilt from the copied rows. PostgreSQL takes the plain `ALTER`
path, where the GIN index is untouched.

Revision ID: afa4aef2e4c9
Revises: d6f691ad9b67
Create Date: 2026-08-31 20:31:33.368790

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "afa4aef2e4c9"
down_revision: str | Sequence[str] | None = "d6f691ad9b67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Verbatim from `56268f297c2b`, which is the point: these have to come back exactly as
# they were, or search comes back subtly different from the search that was tested.
_FTS_TRIGGERS = (
    (
        "CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN"
        "  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);"
        "END"
    ),
    (
        "CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN"
        "  INSERT INTO messages_fts(messages_fts, rowid, text)"
        "  VALUES ('delete', old.id, old.text);"
        "END"
    ),
    (
        "CREATE TRIGGER messages_fts_update AFTER UPDATE ON messages BEGIN"
        "  INSERT INTO messages_fts(messages_fts, rowid, text)"
        "  VALUES ('delete', old.id, old.text);"
        "  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);"
        "END"
    ),
)

_TRIGGER_NAMES = ("messages_fts_insert", "messages_fts_delete", "messages_fts_update")


def _drop_search_triggers() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for name in _TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")


def _restore_search_triggers() -> None:
    """Put the triggers back, then rebuild the index they maintain.

    The rebuild is not optional. The rows were copied into a new table while nothing
    was watching, so the shadow table is describing rowids from before the copy.
    """
    if op.get_bind().dialect.name != "sqlite":
        return
    for statement in _FTS_TRIGGERS:
        op.execute(statement)
    op.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")


def upgrade() -> None:
    """Upgrade schema."""
    _drop_search_triggers()
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("author_user_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_messages_author_user_id"), ["author_user_id"], unique=False
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_messages_author_user_id_users"),
            "users",
            ["author_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    _restore_search_triggers()


def downgrade() -> None:
    """Downgrade schema."""
    _drop_search_triggers()
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_messages_author_user_id_users"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_messages_author_user_id"))
        batch_op.drop_column("author_user_id")
    _restore_search_triggers()
