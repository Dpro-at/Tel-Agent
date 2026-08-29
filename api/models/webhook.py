"""Webhooks — how the operator's own software hears what happened.

§B5's row is `id, workspace_id, url, events[], secret`, and Rule 5 is why it matters
more than its size suggests: everything outside Tel-Agent's column is reached through
webhooks and the generic HTTP tool. This table is the mechanism that lets "add one
more integration" have an answer that is not "add one more connector".

**The secret is generated here, never typed.** It signs the payload so the receiver can
tell a real delivery from anything else that found the URL. A person choosing it would
choose badly, and a person pasting it twice into two systems is how it ends up in a
chat log - so it is made once, shown once, and masked afterwards like every other
credential (§B9, `crypto.mask`).

**`events` is a JSON list, not a table.** It is a set of names from a list this
codebase owns, read only alongside its row and never queried across rows. A join table
would buy referential integrity over a vocabulary that a constant already pins.

**No deliveries, no retries, no last-status.** Nothing sends these yet. Recording what
a webhook *is* is this table's job; what happened to a delivery is a different table
with a different lifetime, and it arrives with the sender.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import utc_now_column, workspace_fk
from api.models.encrypted import EncryptedStr

# What a webhook can be told about. Names are `subject.verb`, past tense: the hook
# fires because something already happened, and a receiver that reads the name as an
# instruction is a receiver that will act twice.
WEBHOOK_EVENTS = (
    "conversation.started",
    "conversation.ended",
    "message.received",
    "assistant.changed",
    "knowledge.changed",
)


class Webhook(Base):
    """One URL, the events it is told about, and the secret that signs them."""

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    # A label the operator recognises in a list of six. Not sent anywhere.
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # `EncryptedStr` encrypts on write and decrypts on read, so no route has to
    # remember to - and the one that would forget is the one that leaks.
    secret: Mapped[str] = mapped_column(EncryptedStr, nullable=False)
    # Off is not deleted. A hook switched off during an incident should come back with
    # the same secret, or every receiver has to be reconfigured to resume it.
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[dt.datetime] = utc_now_column()
    updated_at: Mapped[dt.datetime] = utc_now_column()
