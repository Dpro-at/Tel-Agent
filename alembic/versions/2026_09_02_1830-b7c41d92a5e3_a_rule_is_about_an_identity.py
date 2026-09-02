"""A rule is about an identity, not only a number.

Revision ID: b7c41d92a5e3
Revises: 3fdebcd16757
Create Date: 2026-09-02

Milestone 4 renames `rules.e164_or_pattern` to `pattern` and widens it from 20
characters to 320: a rule is now written against any channel identity - an email
address is the longest of them - and the old name would have lied about the
column's contents forever. Stored values are untouched; an E.164 is a valid
identity.

`batch_alter_table`, because SQLite cannot alter a column in place and rebuilds
the table. `rules` carries no triggers, so the FTS guard in
tests/test_migrations.py has nothing to say here - `messages_fts` belongs to
`messages`, which this migration never touches.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7c41d92a5e3"
down_revision = "3fdebcd16757"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("rules") as batch:
        batch.alter_column(
            "e164_or_pattern",
            new_column_name="pattern",
            existing_type=sa.String(20),
            type_=sa.String(320),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Values longer than 20 characters would be truncated by a stricter dialect;
    # PostgreSQL refuses instead. Downgrading past a rule written against an email
    # address means deleting that rule first, which is the honest requirement.
    with op.batch_alter_table("rules") as batch:
        batch.alter_column(
            "pattern",
            new_column_name="e164_or_pattern",
            existing_type=sa.String(320),
            type_=sa.String(20),
            existing_nullable=False,
        )
