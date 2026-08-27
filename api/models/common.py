"""Building blocks every model reuses.

The choices here apply to all ten tables, so they are made once and explained once.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column


# Every enumerated column is stored as text with a CHECK constraint rather than as a
# native database enum.
#
# D-029 requires the same schema on both dialects. SQLite has no enum type at all, and
# PostgreSQL's is a first-class type that needs its own migration to add a value to —
# so a native enum would mean two different schemas and a migration that can only be
# written for one of them. Text plus CHECK behaves identically on both.
def enum_column(*values: str, name: str) -> Enum:
    # `create_constraint=True` is not optional and is not the default.
    #
    # SQLAlchemy 2.0 defaults it to False, which produces a bare VARCHAR: the ORM
    # rejects an unknown value, and the database accepts anything anyone writes to it
    # with SQL. That is a constraint that only exists while every writer happens to be
    # this application - which is exactly when a constraint stops being one.
    return Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )


def utc_now_column() -> Mapped[dt.datetime]:
    """A timestamp the database fills in, in UTC.

    `server_default` rather than a Python default: rows written by a migration, by a
    seed script or by hand in psql get the same treatment as rows written by the API.
    """
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


def workspace_fk(**kwargs: Any) -> Mapped[int]:
    """The tenant key — D-028.

    On every table that holds data, not just the ones that obviously need it. A query
    that forgets this column leaks one customer's conversations into another
    customer's screen, so the column exists everywhere and the enforcement lives in one
    place rather than being repeated at each call site.

    `ondelete="CASCADE"`: deleting a workspace must not leave orphaned transcripts
    behind. Those are personal data under GDPR, and a row nothing points at is a row
    nobody remembers to delete.
    """
    return mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        **kwargs,
    )
