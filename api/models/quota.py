"""Counters for things that are allowed but must not be unlimited.

`auth_attempts` counts **failures** and locks with an exponential backoff, because a
wrong password is evidence. This counts **successes** against a ceiling, because a chat
message is not evidence of anything - it is ordinary, and the only question is how many
of them per minute.

The two do not share a table. Squeezing a quota into a failure counter would mean a
`failures` column holding something that is not a failure and a `locked_until` that
never locks, and the next person to read either would have to know which rows meant
which.

**Stored rather than held in memory**, for the reason `auth_attempts` gives: a restart
must not hand anybody a fresh budget, and `--reload` in development restarts constantly.
When this needs to survive more than one process it becomes Redis, and the shape here -
one row per bucket, one window - is what that would replace.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import utc_now_column


class RateCounter(Base):
    """One bucket's count, inside one window."""

    __tablename__ = "rate_counters"

    id: Mapped[int] = mapped_column(primary_key=True)
    # What is being counted, as `family:kind:value` - `webchat:conversation:<handle>`.
    #
    # No workspace column: a bucket key is not always inside a workspace. The origin
    # bucket is the clearest case - the whole point of counting an origin is that the
    # request has not been attributed to anybody yet.
    bucket: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    window_started_at: Mapped[dt.datetime] = utc_now_column()
    count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
