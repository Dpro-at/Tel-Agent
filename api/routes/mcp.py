"""The MCP endpoint — Milestone 7, a thin layer over what the API already serves.

The roadmap's sentence is the whole design: *"a thin layer over the REST API, with
hard limits. An external model that can start real conversations — and later real
calls — spends real money."* So this module adds no capability of its own. Every tool
below reads or writes through the same helpers the dashboard's routes use, scoped to
the workspace of the token that called, and the hard limits are the ones §B9.1
already built: its own credential (`api/security/machine_tokens.py`, #123), separate
from the dashboard session, at 120 requests a minute per token and 30 a minute for a
caller holding nothing valid.

**The transport is Streamable HTTP, kept deliberately stateless.** One route, POST,
JSON-RPC 2.0 in and JSON out. No session id is issued and none is expected — the
protocol allows a server that assigns nothing — and there is no SSE listening
channel, because nothing here produces server-initiated messages; a GET answers 405,
which the protocol also allows. Statelessness is not laziness: a session table for a
protocol handshake would be state to leak, expire and test, guarding nothing.

**Five tools, not twenty.** The roadmap's own note on tools: five precise ones beat
twenty that confuse the model. Four read; one — the whisper — acts, and it is the
same act §A6.7 calls highest value and lowest complexity, guarded the way the
dashboard's whisper endpoint is. The write tools the design gallery drew
(`update_assistant`, `set_routing_rule`) stay unbuilt on purpose, exactly as they
were drawn switched off: a model that can rewrite the persona that answers customers
is a decision for a later milestone, not a side effect of this one.

**What a tool returns is what a `viewer` may see.** The token is workspace-scoped and
carries no user, so the floor for reads is the role whose definition is that it may
only read — which is why `_who` is imported from the conversations routes rather than
re-decided here: on the web channel `external_id` is the visitor's resume handle, and
it must not leave the server through this door either (#127).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.config import get_settings
from api.conversations import (
    conversations_for,
    message_counts,
    position_ms,
    previews,
    search_filter,
)
from api.models import Assistant, Channel, Conversation, Message, User
from api.routes.conversations import _utc, _who
from api.system import status as system_status

logger = logging.getLogger("api.mcp")

router = APIRouter(tags=["mcp"])

# The newest protocol revision this server knows. A client asking for a version we
# recognise gets it back; anything else gets this one, which is the negotiation the
# protocol specifies - the client disconnects if it cannot live with the answer.
PROTOCOL_VERSION = "2025-06-18"
KNOWN_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")

# Ceilings on what one tool call may return. The per-minute request limit lives in
# `machine_tokens.PER_TOKEN`; these keep any single answer from being a bulk export -
# an external model asking for "the conversations" should get a page, like a screen.
LIST_LIMIT = 20
TRANSCRIPT_LIMIT = 200

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


def _result(id_: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _tool_text(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    """One tool answer, as the protocol shapes it.

    The text content is the JSON itself: every client renders `text`, and a model
    reads JSON in a text block as well as it reads anything. `structuredContent`
    carries the same value for clients that use it - the protocol says the two must
    agree, and building both from one object is what makes that true by construction.
    """
    body: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": is_error,
    }
    if not is_error and isinstance(payload, dict):
        body["structuredContent"] = payload
    return body


def _refusal(message: str) -> dict[str, Any]:
    """A tool that cannot do what was asked, said inside the result.

    The protocol separates protocol errors (unknown tool, bad arguments) from tool
    failures: the first is a JSON-RPC error, the second is a result with `isError`,
    so the model can read the sentence and try something else.
    """
    return _tool_text({"error": message}, is_error=True)


# --- The tools ---------------------------------------------------------------


async def _list_conversations(
    db: DbSession, workspace_id: int, args: dict[str, Any]
) -> dict[str, Any]:
    query = conversations_for(workspace_id).join(Channel, Channel.id == Conversation.channel_id)
    if args.get("channel"):
        query = query.where(Channel.kind == str(args["channel"]))
    if args.get("status") in ("open", "closed"):
        query = query.where(Conversation.status == args["status"])
    q = str(args.get("q") or "").strip()
    if q:
        matching = (
            select(Message.conversation_id)
            .where(Message.workspace_id == workspace_id)
            .where(search_filter(db, q))
        )
        query = query.where(Conversation.id.in_(matching))

    limit = max(1, min(int(args.get("limit") or 10), LIST_LIMIT))
    rows = list(
        (
            await db.execute(
                query.add_columns(Channel.kind)
                .order_by(Conversation.started_at.desc(), Conversation.id.desc())
                .limit(limit)
            )
        ).all()
    )
    ids = [row[0].id for row in rows]
    preview_by_id = await previews(db, ids)
    count_by_id = await message_counts(db, ids)
    return {
        "conversations": [
            {
                "id": row[0].id,
                "channel": row[1],
                "status": row[0].status,
                "handling": row[0].handling,
                "intent": row[0].intent,
                "started_at": _utc(row[0].started_at),
                "ended_at": _utc(row[0].ended_at),
                # `_who` is the conversations routes' own redaction: on the web
                # channel the id is the visitor's resume handle and never leaves.
                "who": _who(row[0], row[1]),
                "preview": preview_by_id.get(row[0].id),
                "message_count": count_by_id.get(row[0].id, 0),
            }
            for row in rows
        ]
    }


async def _get_conversation(
    db: DbSession, workspace_id: int, args: dict[str, Any]
) -> dict[str, Any]:
    row = await db.scalar(
        conversations_for(workspace_id).where(Conversation.id == int(args["id"]))
    )
    if row is None:
        # Another workspace's id answers like one that does not exist - the same
        # sentence the REST route uses, for the same reason.
        return _refusal("No such conversation.")

    kind = await db.scalar(select(Channel.kind).where(Channel.id == row.channel_id))
    lines = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == row.id)
                .order_by(Message.ts_ms, Message.id)
                .limit(TRANSCRIPT_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    author_ids = {m.author_user_id for m in lines if m.author_user_id is not None}
    names: dict[int, str] = {}
    if author_ids:
        found = await db.execute(select(User.id, User.username).where(User.id.in_(author_ids)))
        names = dict(found.all())

    return {
        "id": row.id,
        "channel": kind,
        "status": row.status,
        "handling": row.handling,
        "intent": row.intent,
        "summary": row.summary,
        "started_at": _utc(row.started_at),
        "ended_at": _utc(row.ended_at),
        "who": _who(row, kind or ""),
        "messages": [
            {
                "ts_ms": m.ts_ms,
                "speaker": m.speaker,
                "text": m.text,
                "is_whisper": m.is_whisper,
                "author": names.get(m.author_user_id) if m.author_user_id else None,
            }
            for m in lines
        ],
    }


async def _list_assistants(
    db: DbSession, workspace_id: int, args: dict[str, Any]
) -> dict[str, Any]:
    rows = (
        (
            await db.execute(
                select(Assistant)
                .where(Assistant.workspace_id == workspace_id)
                .order_by(Assistant.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "assistants": [
            {
                "id": row.id,
                "name": row.name,
                "role": row.role,
                "status": row.status,
                "language": row.language,
                "persona": row.persona,
                "tools": list(row.tools or []),
            }
            for row in rows
        ]
    }


async def _system_health(
    db: DbSession, workspace_id: int, args: dict[str, Any]
) -> dict[str, Any]:
    """The verdict and the per-service states — and deliberately nothing more.

    The REST detail behind the health screen is `admin`-gated because it names hosts
    and paths. A machine token is not an administrator, so this returns what the
    public `/health` word and the screen's row colours say: which service, in which
    state. The reasons stay on the dashboard.
    """
    collected = await system_status.collect(db, get_settings())
    return {
        "verdict": collected["verdict"],
        # `id` and `state` only. The row also carries `detail`, which is where the
        # admin screen's hostnames and paths live - that field stays behind.
        "services": [
            {"name": service["id"], "state": service["state"]}
            for service in collected["services"]
        ],
    }


async def _whisper(db: DbSession, workspace_id: int, args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text") or "").strip()
    if not text:
        return _refusal("A whisper needs something in it.")

    row = await db.scalar(
        conversations_for(workspace_id).where(Conversation.id == int(args["conversation_id"]))
    )
    if row is None:
        return _refusal("No such conversation.")
    if row.status != "open":
        return _refusal("This conversation has ended, so nothing is listening to a whisper.")

    line = Message(
        workspace_id=workspace_id,
        conversation_id=row.id,
        ts_ms=position_ms(row.started_at),
        speaker="human",
        text=text,
        is_whisper=True,
        # No user stands behind a machine token, and inventing one would put a name
        # in the transcript that nobody carries. The archive shows it as a whisper
        # with no author, which is the honest record of a machine having coached.
        author_user_id=None,
        stt_confidence=None,
        language=None,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    logger.info("mcp whisper written into conversation %s", row.id)
    return {"written": True, "message_id": line.id, "ts_ms": line.ts_ms}


# name → (description, input schema, handler). The description is written for the
# model that will read it, which is why each says what comes back and what is refused.
TOOLS: dict[str, tuple[str, dict[str, Any], Any]] = {
    "list_conversations": (
        "The most recent conversations in this workspace, newest first. Filter by "
        "channel kind (web, phone, …), status (open or closed), or full-text search "
        "over what was said. Returns at most 20.",
        {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "maxLength": 40},
                "status": {"type": "string", "enum": ["open", "closed"]},
                "q": {"type": "string", "maxLength": 200},
                "limit": {"type": "integer", "minimum": 1, "maximum": LIST_LIMIT},
            },
            "additionalProperties": False,
        },
        _list_conversations,
    ),
    "get_conversation": (
        "One conversation with its transcript, oldest line first. Lines flagged "
        "is_whisper are operator instructions the customer never saw.",
        {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
        _get_conversation,
    ),
    "list_assistants": (
        "The assistants configured in this workspace: name, role, status, language, "
        "persona and enabled tools.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _list_assistants,
    ),
    "system_health": (
        "Whether this installation is healthy: one verdict (ok, degraded, down) and "
        "a state per service. Not configured means the feature is not set up yet.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        _system_health,
    ),
    "whisper": (
        "Coach the agent inside an open conversation. The agent is told; the "
        "customer never sees it. Refused when the conversation has ended.",
        {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "integer"},
                "text": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
            "required": ["conversation_id", "text"],
            "additionalProperties": False,
        },
        _whisper,
    ),
}


def _tool_listing() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": description, "inputSchema": schema}
        for name, (description, schema, _) in TOOLS.items()
    ]


# --- The JSON-RPC layer -------------------------------------------------------


def _initialize(params: dict[str, Any]) -> dict[str, Any]:
    asked = str(params.get("protocolVersion") or "")
    return {
        "protocolVersion": asked if asked in KNOWN_VERSIONS else PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "Tel-Agent", "version": get_settings().version},
    }


async def _tools_call(
    db: DbSession, workspace_id: int, id_: Any, params: dict[str, Any]
) -> dict[str, Any]:
    name = str(params.get("name") or "")
    if name not in TOOLS:
        return _error(id_, _INVALID_PARAMS, f"Unknown tool: {name!r}.")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(id_, _INVALID_PARAMS, "Tool arguments must be an object.")

    _, schema, handler = TOOLS[name]
    missing = [key for key in schema.get("required", []) if key not in arguments]
    if missing:
        return _error(id_, _INVALID_PARAMS, f"Missing arguments: {', '.join(missing)}.")

    try:
        answer = await handler(db, workspace_id, arguments)
    except (KeyError, TypeError, ValueError):
        # An argument of the wrong shape - an id that is not a number, a filter that
        # is not a string. The schema said so; the model gets the code that means
        # "read the schema again" rather than a stack trace. Logged with the
        # traceback, because this clause also catches a handler's own fault and a
        # line without one cannot tell the two apart.
        logger.info("mcp tool arguments refused", extra={"tool": name}, exc_info=True)
        return _error(id_, _INVALID_PARAMS, "Arguments do not match the tool's schema.")

    if "isError" in answer:
        # A handler that already shaped its own tool answer (the refusals).
        return _result(id_, answer)
    return _result(id_, _tool_text(answer))


async def _dispatch(db: DbSession, workspace_id: int, message: Any) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out - or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, _INVALID_REQUEST, "Not a JSON-RPC 2.0 message.")

    method = message.get("method")
    id_ = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if "id" not in message:
        # A notification. `notifications/initialized` is the only one expected, and
        # the correct answer to any of them is silence.
        return None

    if not isinstance(method, str):
        return _error(id_, _INVALID_REQUEST, "A request needs a method.")

    if method == "initialize":
        return _result(id_, _initialize(params))
    if method == "ping":
        return _result(id_, {})
    if method == "tools/list":
        return _result(id_, {"tools": _tool_listing()})
    if method == "tools/call":
        return await _tools_call(db, workspace_id, id_, params)

    return _error(id_, _METHOD_NOT_FOUND, f"Method not supported: {method}.")


@router.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request) -> Response:
    """The one route. Authentication happened in the middleware (#123): only a
    token minted with the `mcp` scope reaches this line, and `workspace_id` on the
    request state is that token's workspace.

    Left out of the OpenAPI document on purpose: that document describes the REST
    surface, and a JSON-RPC endpoint drawn as one POST operation with a free-form
    body would document nothing while implying the wrong contract. The MCP protocol
    carries its own discovery - `tools/list` *is* the documentation.
    """
    db: DbSession = request.state.db
    workspace_id: int = request.state.workspace_id

    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_response(_error(None, _PARSE_ERROR, "The body is not JSON."))

    # A batch is a 2025-03-26 shape; a single message is every revision. Both are
    # loops over the same dispatch, so supporting both costs nothing.
    if isinstance(body, list):
        if not body:
            return _json_response(_error(None, _INVALID_REQUEST, "An empty batch."))
        answers = [
            answer
            for message in body
            if (answer := await _dispatch(db, workspace_id, message)) is not None
        ]
        if not answers:
            return Response(status_code=202)
        return _json_response(answers)

    answer = await _dispatch(db, workspace_id, body)
    if answer is None:
        # A lone notification: accepted, and there is nothing to say back.
        return Response(status_code=202)
    return _json_response(answer)


def _json_response(payload: Any) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False),
        media_type="application/json",
    )
