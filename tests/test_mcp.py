"""The MCP endpoint — Milestone 7's thin layer, and what it must not hand out.

The gate itself (§B9.1's token, the identical refusals, the ceilings) is
`tests/test_machine_tokens.py`'s subject. These tests start on the other side of it:
a token with the `mcp` scope is past the door, and the questions are the protocol
ones — does the handshake answer, does `tools/list` say what exists, does a tool
answer with the token's workspace and nobody else's — and the one security question
this layer adds: a visitor's resume handle must not leave through this door either
(#127), and the health answer must not name hosts the way the admin route may.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import (
    Assistant,
    Channel,
    Conversation,
    Membership,
    Message,
    User,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


async def _thread(
    db: AsyncSession,
    workspace: Workspace,
    channel: Channel,
    *,
    status: str = "open",
    said: str = "Is the quote from last week still good?",
) -> Conversation:
    row = Conversation(
        workspace_id=workspace.id,
        channel_id=channel.id,
        direction="inbound",
        external_id="resume-handle-" + str(workspace.id),
        status=status,
    )
    db.add(row)
    await db.flush()
    db.add(
        Message(
            workspace_id=workspace.id,
            conversation_id=row.id,
            ts_ms=1000,
            speaker="caller",
            text=said,
        )
    )
    return row


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces with a thread each, an assistant, and a token for one of them."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    web = Channel(workspace_id=mine.id, kind="web", name="Website")
    other = Channel(workspace_id=theirs.id, kind="web", name="Website")
    migrated.add_all([web, other])
    await migrated.flush()

    threads = {
        "live": await _thread(migrated, mine, web),
        "ended": await _thread(migrated, mine, web, status="closed"),
        "theirs": await _thread(migrated, theirs, other, said="Their secret plans."),
    }
    ids = {name: row.id for name, row in threads.items()}

    migrated.add(
        Assistant(
            workspace_id=mine.id,
            name="Reception",
            template="reception",
            status="active",
            persona="Friendly and precise.",
            instructions="Answer briefly.",
        )
    )

    user = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role="admin"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        admin = AsyncClient(transport=transport, base_url="http://localhost")
        assert (
            await admin.post(
                "/api/auth/login", json={"username": "mohamed", "password": PASSWORD}
            )
        ).status_code == 200
        minted = await admin.post("/api/tokens", json={"name": "A model", "scope": "mcp"})
        assert minted.status_code == 201, minted.text
        token = minted.json()["token"]

        machine = AsyncClient(
            transport=transport,
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            yield machine, ids, migrated
        finally:
            await machine.aclose()
            await admin.aclose()


def _request(method: str, id_: int = 1, **params) -> dict:
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params:
        body["params"] = params
    return body


async def _call(machine: AsyncClient, tool: str, **arguments) -> dict:
    answer = await machine.post(
        "/mcp", json=_request("tools/call", name=tool, arguments=arguments)
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


def _payload(response: dict) -> dict:
    """The tool's structured answer out of a JSON-RPC response."""
    import json as _json

    result = response["result"]
    return result.get("structuredContent") or _json.loads(result["content"][0]["text"])


async def test_the_handshake_names_the_server_and_its_tools_capability(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post(
        "/mcp",
        json=_request("initialize", protocolVersion="2025-06-18", capabilities={}),
    )
    assert answer.status_code == 200
    result = answer.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "Tel-Agent"


async def test_an_unknown_protocol_version_gets_the_one_we_speak(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post(
        "/mcp", json=_request("initialize", protocolVersion="1999-01-01")
    )
    assert answer.json()["result"]["protocolVersion"] == "2025-06-18"


async def test_the_initialized_notification_is_accepted_silently(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert answer.status_code == 202
    assert answer.content == b""


async def test_tools_list_names_the_five_and_each_carries_a_schema(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post("/mcp", json=_request("tools/list"))
    tools = answer.json()["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "list_conversations",
        "get_conversation",
        "list_assistants",
        "system_health",
        "whisper",
    ]
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


async def test_conversations_come_from_the_tokens_workspace_alone(stage) -> None:
    machine, ids, _ = stage
    listed = _payload(await _call(machine, "list_conversations"))
    returned = {row["id"] for row in listed["conversations"]}
    assert ids["live"] in returned
    assert ids["ended"] in returned
    assert ids["theirs"] not in returned


async def test_a_web_visitors_resume_handle_never_leaves_through_this_door(stage) -> None:
    """#127's rule, holding at the second exit."""
    machine, ids, _ = stage
    listed = _payload(await _call(machine, "list_conversations"))
    assert all(row["who"] is None for row in listed["conversations"])

    one = _payload(await _call(machine, "get_conversation", id=ids["live"]))
    assert one["who"] is None


async def test_the_transcript_reads_oldest_first(stage) -> None:
    machine, ids, _ = stage
    one = _payload(await _call(machine, "get_conversation", id=ids["live"]))
    assert one["id"] == ids["live"]
    assert [line["text"] for line in one["messages"]] == [
        "Is the quote from last week still good?"
    ]


async def test_another_workspaces_conversation_answers_like_a_missing_one(stage) -> None:
    machine, ids, _ = stage
    answer = await _call(machine, "get_conversation", id=ids["theirs"])
    assert answer["result"]["isError"] is True
    assert "No such conversation" in answer["result"]["content"][0]["text"]


async def test_the_assistants_of_this_workspace_are_readable(stage) -> None:
    machine, _, _ = stage
    listed = _payload(await _call(machine, "list_assistants"))
    assert [row["name"] for row in listed["assistants"]] == ["Reception"]


async def test_health_says_states_and_never_hosts(stage) -> None:
    """The admin route names hosts and paths; a machine token is not an admin."""
    machine, _, _ = stage
    health = _payload(await _call(machine, "system_health"))
    assert health["verdict"] in ("ok", "degraded", "down")
    assert health["services"]
    for service in health["services"]:
        assert set(service) == {"name", "state"}


async def test_a_whisper_lands_in_the_transcript_with_no_invented_author(stage) -> None:
    machine, ids, db = stage
    answer = _payload(
        await _call(machine, "whisper", conversation_id=ids["live"], text="Offer Thursday.")
    )
    assert answer["written"] is True

    row = await db.scalar(select(Message).where(Message.id == answer["message_id"]))
    assert row is not None
    assert row.is_whisper is True
    assert row.speaker == "human"
    assert row.author_user_id is None


async def test_a_whisper_into_an_ended_conversation_is_refused(stage) -> None:
    machine, ids, _ = stage
    answer = await _call(machine, "whisper", conversation_id=ids["ended"], text="Too late.")
    assert answer["result"]["isError"] is True


async def test_an_unknown_tool_is_a_protocol_error_not_a_tool_answer(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post(
        "/mcp", json=_request("tools/call", name="place_call", arguments={})
    )
    assert answer.json()["error"]["code"] == -32602


async def test_arguments_of_the_wrong_shape_point_back_at_the_schema(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post(
        "/mcp",
        json=_request("tools/call", name="get_conversation", arguments={"id": "not-a-number"}),
    )
    assert answer.json()["error"]["code"] == -32602


async def test_an_unknown_method_answers_method_not_found(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post("/mcp", json=_request("resources/list"))
    assert answer.json()["error"]["code"] == -32601


async def test_a_body_that_is_not_json_answers_a_parse_error(stage) -> None:
    machine, _, _ = stage
    answer = await machine.post(
        "/mcp", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert answer.status_code == 200
    assert answer.json()["error"]["code"] == -32700


async def test_a_batch_is_answered_in_kind(stage) -> None:
    """The 2025-03-26 shape: two requests in, two answers out, order kept."""
    machine, _, _ = stage
    answer = await machine.post(
        "/mcp", json=[_request("ping", id_=1), _request("tools/list", id_=2)]
    )
    body = answer.json()
    assert isinstance(body, list)
    assert [item["id"] for item in body] == [1, 2]


async def test_a_get_is_not_part_of_this_transport(stage) -> None:
    """No SSE listening channel — the protocol allows a plain 405."""
    machine, _, _ = stage
    assert (await machine.get("/mcp")).status_code == 405
