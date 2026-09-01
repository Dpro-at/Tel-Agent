"""The tools the agent may use, bound to a conversation — Milestone 5, §B7.

`agent/` may not import `api/`, so a tool that touches the database is built here:
the schema and the words live with the tool, and the body is a closure holding what
the agent side must never hold — a session factory, a workspace, a conversation.
Every body opens its own short session (`session_scope`), because tools run in the
middle of a generation whose caller's session may already be executing, and two
coroutines on one `AsyncSession` is the failure the health probe's comment warns
about.

§B7's own words shape the set: *five precise tools beat twenty that confuse the
model*. `take_message` stays what it was; these are the rest of the table, with the
two phone-named ones carrying the meaning D-017 gave them — a call is a conversation,
so `transfer_call` hands this conversation to a person (the takeover, §A6.7) and
`end_call` is the polite close on any channel. `check_calendar` is not here: the
calendar it needs does not exist yet, and the assistants screen already says so.

**Every result is a sentence the model can act on**, including the refusals: a tool
that raises leaves a customer mid-conversation with a broken page, and a tool that
lies ("done!" over a failed insert) is worse. And nothing here trusts the model's
arguments further than the caller of a public form would - they are text a language
model produced, validated like §B14 validates a stranger's.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent.tools import BUILTIN, Tool
from api import webhooks
from api.db import session_scope
from api.models import Conversation, Knowledge
from api.notifications import raise_notification

logger = logging.getLogger("api.agent_tools")

# What one knowledge hit hands the model. Enough to answer from, small enough that
# three of them do not crowd the question out of the context.
SNIPPET_CHARS = 700
KNOWLEDGE_HITS = 3

# The generic escape hatch's ceilings. A tool the model drives must not become a
# download manager or a port scanner: one request, ten seconds, no redirects (a
# redirect is a destination the operator never allowed), and an answer trimmed to
# what a model can actually read.
HTTP_TIMEOUT = 10.0
HTTP_BODY_MAX = 4000

REASON_MAX = 500


def _snippet(content: str, query_terms: list[str]) -> str:
    """The part of the document nearest the first term that appears in it."""
    lowered = content.lower()
    at = min(
        (found for term in query_terms if (found := lowered.find(term.lower())) >= 0),
        default=0,
    )
    start = max(0, at - SNIPPET_CHARS // 3)
    piece = content[start : start + SNIPPET_CHARS].strip()
    return (
        ("…" if start else "") + piece + ("…" if start + SNIPPET_CHARS < len(content) else "")
    )


def toolset(
    sessionmaker: async_sessionmaker,
    *,
    workspace_id: int,
    conversation_id: int,
) -> list[Tool]:
    """Everything this conversation's agent may do.

    Built per reply rather than at import, because the closures carry the
    conversation - which is also what makes `transfer_call` and `end_call` act on
    the right thread without the model being trusted to name one.
    """

    async def search_knowledge(arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return "Say what to search for."
        terms = [term for term in query.split() if len(term) >= 2][:8]
        if not terms:
            return "Say what to search for."
        async with session_scope(sessionmaker) as db:
            # LIKE over a per-workspace handful of documents, not the messages FTS
            # index: knowledge is dozens of rows where messages are thousands, and a
            # match on any term beats a match on the whole phrase for the way models
            # phrase queries.
            filters = [
                or_(Knowledge.content.ilike(f"%{term}%"), Knowledge.title.ilike(f"%{term}%"))
                for term in terms
            ]
            rows = (
                (
                    await db.execute(
                        select(Knowledge)
                        .where(Knowledge.workspace_id == workspace_id, or_(*filters))
                        .limit(KNOWLEDGE_HITS)
                    )
                )
                .scalars()
                .all()
            )
        if not rows:
            return (
                "Nothing in the knowledge sources matches that. Do not guess - say you "
                "do not know, or offer to take a message."
            )
        found = "\n\n".join(f"[{row.title}]\n{_snippet(row.content, terms)}" for row in rows)
        return f"From this business's own documents:\n\n{found}"

    async def http_request(arguments: dict[str, Any]) -> str:
        from api.settings import store

        url = str(arguments.get("url") or "").strip()
        method = str(arguments.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            return "Only GET and POST are allowed."
        async with session_scope(sessionmaker) as db:
            allowed_raw = str(
                await store.get(db, "http_tool.allowed_urls", workspace_id=workspace_id) or ""
            )
        prefixes = [prefix.strip() for prefix in allowed_raw.split(",") if prefix.strip()]
        if not prefixes:
            return (
                "No addresses are allowed yet. The operator adds them in Settings "
                "under the HTTP tool's allowed addresses."
            )
        if not any(url.startswith(prefix) for prefix in prefixes):
            # The allowlist is the whole of the safety here: the model chooses the
            # URL, and an unlisted one could be this installation's own loopback.
            return "That address is not on the allowed list."

        body = arguments.get("body")
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT, follow_redirects=False
            ) as client:
                if method == "GET":
                    response = await client.get(url)
                else:
                    response = await client.post(
                        url, json=body if isinstance(body, dict | list) else None
                    )
        except httpx.HTTPError as error:
            return f"The request failed: {str(error)[:200]}"
        text = response.text[:HTTP_BODY_MAX]
        return f"HTTP {response.status_code}:\n{text}"

    async def send_notification(arguments: dict[str, Any]) -> str:
        reason = str(arguments.get("reason") or "").strip()[:REASON_MAX]
        if not reason:
            return "Say what to tell them."
        async with session_scope(sessionmaker) as db:
            await raise_notification(
                db,
                workspace_id=workspace_id,
                category="review",
                message_key="agent_notification",
                params={},
                detail=reason,
                primary_action="open_conversation",
                action_payload={"conversation_id": conversation_id},
                conversation_id=conversation_id,
            )
        return "They have been told. Carry on with the customer."

    async def transfer_call(arguments: dict[str, Any]) -> str:
        reason = str(arguments.get("reason") or "").strip()[:REASON_MAX]
        async with session_scope(sessionmaker) as db:
            row = await db.scalar(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            if row is None or row.status != "open":
                return "This conversation has already ended."
            # §A6.7's takeover, agent-initiated: from the next message the agent is
            # silent and the live screen's reply box is where answers come from.
            row.handling = "human"
            await db.commit()
            await raise_notification(
                db,
                workspace_id=workspace_id,
                category="review",
                message_key="transfer_requested",
                params={},
                detail=reason or None,
                needs_decision=True,
                primary_action="open_conversation",
                action_payload={"conversation_id": conversation_id},
                conversation_id=conversation_id,
            )
        logger.info("agent transferred conversation %s to a person", conversation_id)
        return (
            "A person has been asked to take over. Tell the customer someone will "
            "continue this conversation shortly, then stop."
        )

    async def end_call(arguments: dict[str, Any]) -> str:
        import datetime as dt

        async with session_scope(sessionmaker) as db:
            row = await db.scalar(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            if row is not None and row.status == "open":
                row.status = "closed"
                # The column is timezone-naive and read back through `_utc()`, so
                # what is stored is bare UTC - an aware value breaks on PostgreSQL.
                row.ended_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
                # `end_call` is the one writer of `status='closed'`, which makes it
                # the emitter of `conversation.ended` - queued in the same session as
                # the close, so neither can be observed without the other.
                await webhooks.queue(
                    db,
                    workspace_id=workspace_id,
                    event="conversation.ended",
                    data={
                        "conversation": row.external_id,
                        "ended_at": row.ended_at.isoformat(),
                    },
                )
                await db.commit()
        logger.info("agent closed conversation %s", conversation_id)
        return (
            "The conversation is closed. Say a brief goodbye - nothing after it "
            "will be answered."
        )

    return [
        *BUILTIN,
        Tool(
            name="search_knowledge",
            description=(
                "Search this business's own uploaded documents - opening hours, "
                "prices, policies, product details. Use it before answering any "
                "question specific to this business; never guess what it might say."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A few search words, in the document's likely language.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            run=search_knowledge,
        ),
        Tool(
            name="http_request",
            description=(
                "Call one of the operator's pre-approved web addresses - an ordering "
                "system, a booking service. Only for addresses the operator has "
                "allowed; anything else is refused. Do not use it to browse."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full address to call."},
                    "method": {"type": "string", "enum": ["GET", "POST"]},
                    "body": {
                        "type": "object",
                        "description": "JSON body, for POST.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            run=http_request,
        ),
        Tool(
            name="send_notification",
            description=(
                "Tell the people running this business something right now, without "
                "ending the conversation - a complaint, something urgent, something "
                "they said to flag. Not for taking messages: use take_message when "
                "the customer wants a call back."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "What they should know, briefly.",
                    }
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
            run=send_notification,
        ),
        Tool(
            name="transfer_call",
            description=(
                "Hand this conversation to a person. Use it when the customer asks "
                "for one, or when you cannot help and a message is not enough. After "
                "calling it, tell them someone will continue shortly."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why, briefly - the person taking over reads this.",
                    }
                },
                "additionalProperties": False,
            },
            run=transfer_call,
        ),
        Tool(
            name="end_call",
            description=(
                "Close this conversation politely once the customer is done and has "
                "nothing further. Never call it mid-question or to avoid answering."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            run=end_call,
        ),
    ]
