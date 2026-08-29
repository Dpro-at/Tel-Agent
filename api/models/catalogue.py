"""The catalogue — what the business sells, in the words the assistant may use.

§A6.11's first tab. Tel-Agent ships with none of this: an agent that invents a price
is worse than one that says it does not know, so the only prices it can quote are the
ones somebody typed here.

**Money is integer micros, never a float.** CLAUDE.md's data-model rule, for the reason
every billing system eventually learns: 0.1 + 0.2 is not 0.3 in binary floating point,
and a price read back different from the price entered is a price nobody trusts. The
currency is one per workspace and lives in the settings registry, not on the row -
a business does not price half its services in euros and half in francs, and a column
that could would have to be validated against itself on every read.

**`price_mode` decides whether `price_micros` means anything.** "On request" is not
zero and not null-with-a-shrug: it is a real answer a business gives, and the screen
says it out loud. Keeping it as a mode rather than inferring it from a null price is
what stops "we have not filled this in yet" from being spoken as "ask us".

**One flag, not two.** The screen's column is "Bookable", and its note says a service
switched off still exists - the assistant says so and asks the caller to arrange it
with a person. That is one state, so it is one column. A separate `active` would let a
row be inactive-but-bookable, which the interface has no way to draw and no meaning for.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, Integer, String, Text, text, true
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk

# How the price is quoted. `fixed` and `hourly` both use `price_micros`; `on_request`
# ignores it, because there is no number to ignore.
PRICE_MODES = ("fixed", "hourly", "on_request")

# Long enough for "Emergency call-out outside business hours", short enough that it is
# still a name rather than a paragraph. The paragraph is `says`.
NAME_MAX = 120

# What the assistant is allowed to say about this service, in the business's own words.
# Capped because it is read aloud on a call: past this it stops being an answer.
SAYS_MAX = 400


class Service(Base):
    """One thing the business sells, and what the assistant may say about it."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    name: Mapped[str] = mapped_column(String(NAME_MAX), nullable=False)
    # The sentence the assistant uses. Null means it has only the name, which is
    # honest: it will say the service exists and nothing more about it.
    says: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Null means no fixed length - "as long as it takes", which some work genuinely is.
    # Minutes rather than a duration type: every calendar this will ever talk to counts
    # in minutes, and a service measured in seconds is not a service.
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # `server_default` as well as `default`, on all three of these. A Python-side
    # default is invisible to anything that is not this ORM, so a row inserted by hand
    # or by a migration backfill hits NOT NULL with nothing to fill it - which is a
    # failure at the least convenient moment rather than at the one that wrote it.
    price_mode: Mapped[str] = mapped_column(
        enum_column(*PRICE_MODES, name="price_mode"),
        nullable=False,
        default="fixed",
        server_default=text("'fixed'"),
    )
    # Integer micros of the workspace's currency. BigInteger because micros of a
    # five-figure price already exceed what a 32-bit column holds.
    price_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Null means "any free" - the common case, and the one the screen shows by default.
    # Free text rather than a foreign key to a user: the person who does the work is
    # often not somebody with a login, and a name is what the caller is told.
    performed_by: Mapped[str | None] = mapped_column(String(NAME_MAX), nullable=True)
    # Whether the assistant may book it on its own. False does not hide the service:
    # it is still quoted and described, and the caller is asked to arrange it with
    # a person - which is what an emergency call-out should always do.
    # `true()` rather than `text("1")`: D-029 says one schema serves both dialects,
    # and `1` is a boolean only in SQLite. SQLAlchemy renders this per dialect.
    bookable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    # The order the business wants them read in. Held rather than sorted by name,
    # because "first consultation" belongs at the top whatever letter it starts with.
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[dt.datetime] = utc_now_column()
    updated_at: Mapped[dt.datetime] = utc_now_column()
