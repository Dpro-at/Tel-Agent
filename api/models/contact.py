"""Contacts — who a number belongs to.

§B5's row: `e164, name, tags, notes`. The phonebook, and nothing grander: matching
a caller to a contact at call time is the agent's job, and the CRM this table must
never grow into is named in Rule 5's right-hand column.

`e164` is unique per workspace - a number that belongs to two people at once is a
question the screen cannot answer. The reverse (one person, two numbers) is two
rows sharing a name, which is exactly what it looks like.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import utc_now_column, workspace_fk


class Contact(Base):
    """One person or business, as a channel knows them."""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("workspace_id", "e164", name="contact_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    e164: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Free words, stored as a list. A closed tag vocabulary is a product decision
    # nobody has made; the screen offers what the workspace already uses.
    tags: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
