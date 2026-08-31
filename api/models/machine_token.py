"""The credentials the machine paths carry — §B9.1's second and third rows.

The specification names three paths that can start a real call and gives each its own
control: the dashboard session, `POST /hooks/call`, and the MCP endpoint. This table is
the second and third. The sentence that shapes it is the one after that table — *a leak
of one must not open the others* — so a row carries the scope it is good for, and one
scope is never read as another.

**Hashed, not encrypted, and that is the difference from `webhooks.secret`.** The
signing secret has to be *readable* to sign a delivery with, so it is encrypted and can
be decrypted. Nothing ever needs to read one of these back: a caller presents it and
the question is only whether it matches. So the row keeps the SHA-256 and a database
dump yields nothing that opens anything. It is the reasoning `sessions.token_hash`
already uses, for a value with the same shape — 256 bits from the system's random
source, looked up on every request, with nothing low-entropy to guess.

**`last_four` exists because of that choice.** A credential that cannot be read back
cannot be masked on the way out either, so the four characters the operator recognises
in a list of six are stored beside the hash rather than derived from it.

**No `enabled` column.** A webhook is switched off during an incident and comes back
with the same secret, because every receiver would otherwise be reconfigured. A
credential in trouble is rotated or removed instead — a *disabled* credential is one
somebody re-enables later without remembering why it was off.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import enum_column, utc_now_column, workspace_fk

# What a token can be good for. One name per machine path, and the list is closed: a
# free-text scope is a scope that will eventually be compared against a typo.
MACHINE_SCOPES = ("hooks", "mcp")


class MachineToken(Base):
    """One credential, the path family it opens, and when it was last used."""

    __tablename__ = "machine_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    # A label the operator recognises. Never sent anywhere, never part of the secret.
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[str] = mapped_column(
        enum_column(*MACHINE_SCOPES, name="machine_token_scope"), nullable=False
    )
    # Unique: two rows with one hash would make "which token was that" unanswerable,
    # and indexed because this is the lookup every machine request pays for.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    created_at: Mapped[dt.datetime] = utc_now_column()
    # Nullable for as long as nobody has presented it. That is the state worth seeing
    # on the screen: a credential minted three months ago and never used is one to
    # remove, and it looks exactly like a working one without this column.
    last_used_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
