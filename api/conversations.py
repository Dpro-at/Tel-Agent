"""Reading conversations, and searching what was said in them.

The transcript archive is one of the five things Rule 5 says Tel-Agent owns, and
`messages` has carried a full-text index since the very first migration — a GIN index
over `to_tsvector('simple', text)` on PostgreSQL, an FTS5 virtual table with three
triggers on SQLite. **Nothing has ever queried either of them.** This module is what
finally does; the index was the same kind of orphan as the four cleanup functions P2
found, and an index nothing uses is a write cost with no read benefit.

**Search is the one query that must be written twice.** D-029 puts both dialects on
equal footing, and full-text search is where they genuinely differ: there is no
portable spelling of it, and the ORM has no abstraction that covers both without
falling back to `LIKE`. `LIKE '%word%'` would be portable, would not use either index,
and would scan every transcript on the installation — so it is written twice, on
purpose, with the two spellings side by side where they can be compared.

The PostgreSQL half must say `'simple'` exactly as the index does. `to_tsvector(text)`
without the configuration argument is a *different expression*, the planner will not
match it to the index, and the query silently becomes a sequential scan over every
message — fast on a laptop with fifty rows and catastrophic on a year of transcripts.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import Select, false, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Conversation, Message

# One page of threads. The screen shows a scrolling list, not a pager, so this is the
# size of a scroll rather than a page of results.
PAGE = 50

# How much of the last message is kept for the list's preview line. The list shows one
# line per thread; sending the whole transcript so the browser can trim it would move
# megabytes to render kilobytes.
PREVIEW = 160


def is_postgres(db: DbSession) -> bool:
    return db.bind.dialect.name == "postgresql"


# A term is a run of letters or digits, in any script - `café` and `فاتورة` are terms.
# Everything else a person can type into a search box is punctuation as far as the
# index is concerned.
_TERM = re.compile(r"\w+", re.UNICODE)


def terms_of(query: str) -> list[str]:
    """The words in a search box, with every operator character discarded.

    Both dialects have a query language, and both raise on input a person can
    reasonably type: a bare `"` is `unterminated string` to SQLite's FTS5, which
    reached the browser as a 500 before this existed. Neither language is one the
    search box promises, so neither is one it has to accept.

    Discarding the operators also settles a second question the two dialects would
    otherwise answer differently. `websearch_to_tsquery` understands `or` and a
    leading minus; FTS5 understands `NEAR` and `*`. Passing input through to both
    would mean the same search box behaving differently depending on which database
    the installation happens to run - which is the opposite of what D-029 is for.
    Every search is "all of these words", on both.
    """
    return _TERM.findall(query)


def search_filter(db: DbSession, query: str) -> Any:
    """The `WHERE` clause that finds messages matching `query`, per dialect.

    Both spellings are parameterised. The query text is a person's search box and
    reaches the database as a bound value, never as concatenated SQL.
    """
    terms = terms_of(query)
    if not terms:
        # Punctuation alone. Matching nothing is the honest answer - a search that
        # quietly became "show everything" would look like a working search that
        # ignores what was typed.
        return false()

    if is_postgres(db):
        # `'simple'` is not a default to be left off: the index is built over
        # `to_tsvector('simple', text)`, and any other spelling of this expression is
        # not the indexed one. Space-separated terms are an AND to
        # `websearch_to_tsquery`, which is the behaviour chosen above.
        return text(
            "to_tsvector('simple', messages.text) @@ websearch_to_tsquery('simple', :q)"
        ).bindparams(q=" ".join(terms))

    # SQLite keeps the index in a separate virtual table kept current by three
    # triggers, so the match is a subquery against it rather than a predicate on the
    # column. `rowid` is `messages.id` - that is what the insert trigger writes.
    #
    # Each term is quoted, which in FTS5 makes it a literal string rather than a
    # fragment of query syntax. The terms contain no quotes to escape: `terms_of`
    # kept only word characters.
    match = " ".join(f'"{term}"' for term in terms)
    return Message.id.in_(
        select(text("rowid"))
        .select_from(text("messages_fts"))
        .where(text("messages_fts MATCH :q").bindparams(q=match))
    )


def conversations_for(workspace_id: int) -> Select:
    """The base query, scoped before anything else is added to it.

    The scope is part of the query rather than a check on the result, for the reason
    D-028 gives: a filter applied afterwards is one somebody eventually forgets, and
    forgetting it here means one customer's transcripts on another customer's screen.
    """
    return select(Conversation).where(Conversation.workspace_id == workspace_id)


async def previews(db: DbSession, conversation_ids: list[int]) -> dict[int, str]:
    """The last line of each conversation, in one query rather than one per row.

    A list of fifty threads asking for its own preview is fifty round trips, and it is
    the shape that looks fine on a seeded database and falls over on a real one.
    """
    if not conversation_ids:
        return {}

    # The greatest `ts_ms` per conversation, then the row that carries it. Two steps
    # because `ts_ms` is not unique on its own - two lines can share a millisecond -
    # and the id breaks the tie deterministically.
    latest = (
        select(Message.conversation_id, func.max(Message.id).label("message_id"))
        .where(Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
        .subquery()
    )
    rows = await db.execute(
        select(Message.conversation_id, Message.text).join(
            latest, Message.id == latest.c.message_id
        )
    )
    return {cid: body[:PREVIEW] for cid, body in rows}


async def message_counts(db: DbSession, conversation_ids: list[int]) -> dict[int, int]:
    """How many lines each conversation holds — again in one query, for one reason."""
    if not conversation_ids:
        return {}
    rows = await db.execute(
        select(Message.conversation_id, func.count(Message.id))
        .where(Message.conversation_id.in_(conversation_ids))
        .group_by(Message.conversation_id)
    )
    return dict(rows.all())
