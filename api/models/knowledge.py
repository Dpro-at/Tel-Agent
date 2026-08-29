"""Knowledge — what an assistant is allowed to read before it answers.

§B5's row is `id, workspace_id, agent_id, title, content, embedding`. Four of those
six are here. The two that are not:

**No `embedding`.** A vector belongs to the model that produced it, and no embedding
pipeline exists yet - a column holding NULL on every row would only be a place for the
first mistake to hide. `content` is the fact; the index over it is a derived thing that
arrives with the retrieval it serves, and adding a column then is a migration rather
than a redesign.

**No crawling, no uploads.** The screen also draws a website being crawled, a PDF being
parsed, and an index rebuilding. Each is its own subsystem with its own failure modes,
and none of them changes what a piece of knowledge *is*: a title and some text this
workspace wants answered from. They land on top of this table, not instead of it.

**`assistant_id` is one assistant or none, following §B5.** The design shows a source
shared by two of three assistants, which this column cannot express - that would be a
join table. The specification wins per CLAUDE.md, and NULL carries the common case the
design is really about: knowledge every assistant may read.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from api.db import Base
from api.models.common import utc_now_column, workspace_fk

# The largest single piece of text a person pastes in one go. Well past a page of
# opening hours or a price list, and short enough that the refusal happens here
# rather than at a model that charges for finding out.
CONTENT_MAX = 20_000


class Knowledge(Base):
    """One thing the assistant may read: a title, and the text under it."""

    __tablename__ = "knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = workspace_fk()
    # NULL means every assistant in the workspace, which is what most knowledge is.
    # `SET NULL` rather than `CASCADE`: deleting an assistant must not silently take
    # the opening hours with it - the text outlives whoever was reading it.
    assistant_id: Mapped[int | None] = mapped_column(
        ForeignKey("assistants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = utc_now_column()
    updated_at: Mapped[dt.datetime] = utc_now_column()
