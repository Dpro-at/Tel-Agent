"""positions, not clocks

`messages.ts_ms` is documented as milliseconds since the conversation started, and both
screens that render it read it that way — the archive adds it to `started_at`, the call
detail draws it as `mm:ss` into the recording. Every writer wrote epoch milliseconds
instead.

Nothing failed, because ordering is unaffected: every row on a thread used the same
clock. Only a rendered timestamp showed it, and it showed a message sent on 2026-08-31 as
**2083-05-01** in the archive and as **29803424:52** on a call.

The writers are fixed in this same change. This migration is the rows already stored, and
it converts them by the only evidence available: a value at or above `10**12` is a wall
clock — about fifty-six years of milliseconds, and no conversation runs that long — so it
becomes `ts_ms - started_at`, floored at zero. A value below that is already a position
and is left alone, which is what makes the migration safe to run twice.

**Order within a thread is preserved exactly**, because every row on it is shifted by the
same `started_at`. That is the property the column exists for, and the one a backfill
could plausibly break.

Irreversible on purpose, and the downgrade says so rather than pretending: the original
epoch value cannot be recovered from a position without trusting the same `started_at`
the upgrade already used, and a downgrade that re-derived it would be guessing at data it
had itself rewritten.

Revision ID: 3fdebcd16757
Revises: afa4aef2e4c9
Create Date: 2026-09-01 13:34:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3fdebcd16757"
down_revision: str | Sequence[str] | None = "afa4aef2e4c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Milliseconds. Anything at or above this is a wall clock rather than a position.
EPOCH_SCALE = 10**12


def upgrade() -> None:
    """Turn stored wall clocks into positions."""
    dialect = op.get_bind().dialect.name

    # The same statement in two dialects, because epoch milliseconds are derived
    # differently and D-029 requires both. SQLite has no EXTRACT and PostgreSQL has no
    # `strftime`; neither has a portable "milliseconds since 1970" for a timestamp.
    # **Rounded, not truncated, and that is not fussiness.** SQLite's `julianday` is a
    # float, and a truncating cast of the product loses the last millisecond: a line
    # seven seconds in came back as 6999. A double carries these values to about a
    # twentieth of a millisecond, so rounding is exact at the resolution stored.
    if dialect == "postgresql":
        started_ms = "ROUND(EXTRACT(EPOCH FROM c.started_at) * 1000)"
    else:
        started_ms = "ROUND((julianday(c.started_at) - 2440587.5) * 86400000)"

    # `MAX` in SQLite is `GREATEST` in PostgreSQL when it takes two scalars — the same
    # floor, spelled differently.
    floor = "MAX" if dialect == "sqlite" else "GREATEST"

    # `BIGINT` rather than `INTEGER`: on PostgreSQL the latter is 32 bits, which caps
    # a position at about twenty-four days and would raise on anything longer rather
    # than storing it wrong. SQLite reads `BIGINT` as integer affinity, so one word
    # serves both. The column is a BigInteger either way.

    # Every interpolated part is a module constant or one of the two strings chosen
    # above. Nothing on this page comes from a request, which is what S608 cannot see.
    statement = f"""
        UPDATE messages
           SET ts_ms = CAST(
                   {floor}(
                       0,
                       ts_ms - (
                           SELECT {started_ms}
                             FROM conversations c
                            WHERE c.id = messages.conversation_id
                       )
                   ) AS BIGINT
               )
         WHERE ts_ms >= {EPOCH_SCALE}
    """  # noqa: S608

    op.execute(statement)


def downgrade() -> None:
    """Deliberately nothing.

    A position cannot be turned back into the wall clock it was without re-deriving it
    from the `started_at` this migration already used — which would be inventing data
    rather than restoring it. The forward direction is safe to repeat, so an installation
    that needs to go back and forward again loses nothing.
    """
