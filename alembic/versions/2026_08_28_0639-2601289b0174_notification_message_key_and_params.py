"""notification message key and params

A notification stores *which* message and *what goes in it*, not a finished sentence.
The screen is translated into three languages and a notification is read on it; prose
written by whatever raised it would arrive in the server's language and stay there.

**Existing rows are deleted, not converted.** A stored sentence has no key to map back
to - the information needed to translate it was never captured - so there is nothing to
migrate it into. That is affordable exactly once: nothing in the product raises a
notification yet, so on any real installation this table is empty. Doing it after the
first caller exists would mean deleting somebody's history.

Revision ID: 2601289b0174
Revises: 74f37035bf6b
Create Date: 2026-08-28 06:39:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2601289b0174"
down_revision: str | Sequence[str] | None = "74f37035bf6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Emptied first, and for two reasons. The rows cannot be translated into the new
    # shape at all, and adding a NOT NULL column with no default to a table that has
    # rows is refused outright by PostgreSQL.
    op.execute(sa.text("DELETE FROM notifications"))

    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("message_key", sa.String(length=64), nullable=False))
        batch_op.add_column(sa.Column("params", sa.JSON(), nullable=False))
        batch_op.drop_column("body")
        batch_op.drop_column("title")


def downgrade() -> None:
    """Downgrade schema."""
    # Same argument in the other direction: a key and its parameters cannot be turned
    # back into a sentence without the translations, which live in the interface.
    op.execute(sa.text("DELETE FROM notifications"))

    with op.batch_alter_table("notifications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("title", sa.VARCHAR(length=200), nullable=False))
        batch_op.add_column(sa.Column("body", sa.TEXT(), nullable=True))
        batch_op.drop_column("params")
        batch_op.drop_column("message_key")
