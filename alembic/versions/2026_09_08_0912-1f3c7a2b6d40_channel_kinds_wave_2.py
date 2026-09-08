"""The fourteen new channel kinds, in one revision.

Revision ID: 1f3c7a2b6d40
Revises: b7c41d92a5e3
Create Date: 2026-09-08

D-044 grows the official channel list. `channels.kind` is text with a CHECK
constraint rather than a native enum (see `api/models/common.py`), which is what
makes one migration serve PostgreSQL and SQLite - and it is also why widening it
means rewriting the constraint rather than adding a value to a type.

**One revision for the whole wave, not one per channel.** Every kind of this wave
lands here, so each channel that follows is a transport module, a descriptor and a
test file, with no schema step of its own.

`sms` and `web` are already in the list from the first migration and are not
repeated. `batch_alter_table` is what carries this to SQLite, which cannot alter a
constraint in place and rebuilds the table instead; on PostgreSQL it is a plain
DROP CONSTRAINT / ADD CONSTRAINT. `channels` carries no triggers, so the full-text
guard in `tests/test_migrations.py` has nothing to say here - `messages_fts`
belongs to `messages`, which this migration never touches.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "1f3c7a2b6d40"
down_revision = "b7c41d92a5e3"
branch_labels = None
depends_on = None

CONSTRAINT = "channel_kind"

BEFORE = (
    "web",
    "phone",
    "sms",
    "email",
    "whatsapp",
    "telegram",
    "messenger",
    "instagram",
    "discord",
    "slack",
)

ADDED = (
    "teams",
    "signal",
    "viber",
    "google_chat",
    "mattermost",
    "matrix",
    "irc",
    "line",
    "wechat",
    "wecom",
    "qq",
    "dingtalk",
    "feishu",
    "imessage",
)

AFTER = BEFORE + ADDED


def _rewrite(values: tuple[str, ...], previous: tuple[str, ...]) -> None:
    # The column is not only constrained, it is also *sized*. `enum_column` renders a
    # non-native enum, which is a VARCHAR as wide as the longest value it was created
    # with - nine characters, from `instagram`. PostgreSQL enforces that length, so
    # `google_chat` (eleven) and `mattermost` (ten) would be rejected by the column
    # itself no matter what the CHECK constraint says. Widening it belongs in the same
    # batch as the constraint, and narrowing it belongs in the downgrade.
    listed = ", ".join(f"'{value}'" for value in values)
    width = max(len(value) for value in values)
    with op.batch_alter_table("channels") as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.alter_column(
            "kind",
            type_=sa.String(length=width),
            existing_type=sa.String(length=max(len(value) for value in previous)),
            existing_nullable=False,
        )
        batch.create_check_constraint(CONSTRAINT, sa.text(f"kind IN ({listed})"))


def upgrade() -> None:
    _rewrite(AFTER, BEFORE)


def downgrade() -> None:
    # A channel of one of the new kinds cannot be described by the old constraint, so
    # it goes first. Deleting the row rather than leaving it is the honest reading of
    # "this installation no longer supports that channel": its conversations are
    # RESTRICTed against deletion, so an installation that actually used one is told
    # to deal with the transcripts rather than losing them silently.
    listed = ", ".join(f"'{value}'" for value in ADDED)
    op.execute(sa.text(f"DELETE FROM channels WHERE kind IN ({listed})"))  # noqa: S608
    _rewrite(BEFORE, AFTER)
