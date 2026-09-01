"""Milestone 5 — the tools, bound to a conversation, and the loop that offers them.

§B7's set, minus the calendar it says must wait. Each tool is exercised through its
own body against a real database (the closures are the product; a mock of them
would test the mock), and the loop's half — that a caller's toolset is what the
model is offered and what a call resolves against — is exercised with a scripted
provider. The rules that repeat across tools: the model's arguments are validated
like a stranger's form input, every failure comes back as a sentence the model can
act on, and nothing raises out of a tool into a customer's open page.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent_tools import toolset
from api.config import Settings
from api.main import create_app
from api.models import (
    Channel,
    Conversation,
    Knowledge,
    Membership,
    Notification,
    User,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """A workspace with an open conversation, a knowledge document, and an admin."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    channel = Channel(workspace_id=mine.id, kind="web", name="Web chat", status="active")
    migrated.add(channel)
    await migrated.flush()

    conversation = Conversation(
        workspace_id=mine.id,
        channel_id=channel.id,
        direction="inbound",
        external_id="visitor-1",
        handling="ai",
        status="open",
    )
    migrated.add(conversation)
    migrated.add(
        Knowledge(
            workspace_id=mine.id,
            title="Opening hours",
            content=(
                "We are open Monday to Friday from 08:00 to 18:00, and on Saturday "
                "until noon. Closed on Sundays and Austrian public holidays."
            ),
        )
    )
    migrated.add(
        Knowledge(
            workspace_id=theirs.id,
            title="Their secret",
            content="Saturday is when Wolf Studio plans the heist.",
        )
    )

    user = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role="admin"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        http = AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://localhost",
        )
        assert (
            await http.post(
                "/api/auth/login", json={"username": "mohamed", "password": PASSWORD}
            )
        ).status_code == 200
        tools = {
            tool.name: tool
            for tool in toolset(
                app.state.sessionmaker,
                workspace_id=mine.id,
                conversation_id=conversation.id,
            )
        }
        try:
            yield http, tools, conversation.id, mine.id, migrated
        finally:
            await http.aclose()


async def _tray(db: AsyncSession) -> list[Notification]:
    db.expire_all()
    return list((await db.execute(select(Notification).order_by(Notification.id))).scalars())


# --- search_knowledge -----------------------------------------------------------


async def test_knowledge_answers_from_this_workspaces_documents_alone(stage) -> None:
    _, tools, _, _, _ = stage
    answer = await tools["search_knowledge"].run({"query": "Saturday opening"})
    assert "Opening hours" in answer
    assert "until noon" in answer
    # The other workspace's document matches "Saturday" and must not appear.
    assert "heist" not in answer


async def test_an_empty_search_and_a_missing_answer_are_both_sentences(stage) -> None:
    _, tools, _, _, _ = stage
    assert "Say what to search for" in await tools["search_knowledge"].run({"query": "  "})
    nothing = await tools["search_knowledge"].run({"query": "quantum chromodynamics"})
    assert "Do not guess" in nothing


# --- http_request ---------------------------------------------------------------


async def test_http_refuses_everything_until_the_operator_allows_addresses(stage) -> None:
    _, tools, _, _, _ = stage
    answer = await tools["http_request"].run({"url": "https://orders.example.com/api"})
    assert "No addresses are allowed yet" in answer


async def test_http_refuses_an_address_off_the_list(stage) -> None:
    """The allowlist is the whole of the safety: the model chooses the URL, and an
    unlisted one could be this installation's own loopback."""
    http, tools, _, _, _ = stage
    saved = await http.patch(
        "/api/settings",
        json={"values": {"http_tool.allowed_urls": "https://orders.example.com/"}},
    )
    assert saved.status_code == 200, saved.text

    refused = await tools["http_request"].run({"url": "http://127.0.0.1:8000/api/setup"})
    assert "not on the allowed list" in refused
    also_refused = await tools["http_request"].run(
        {"url": "https://orders.example.com/api", "method": "DELETE"}
    )
    assert "Only GET and POST" in also_refused


async def test_http_calls_an_allowed_address_and_reports_what_came_back(
    stage, monkeypatch
) -> None:
    http, tools, _, _, _ = stage
    saved = await http.patch(
        "/api/settings",
        json={"values": {"http_tool.allowed_urls": "https://orders.example.com/"}},
    )
    assert saved.status_code == 200, saved.text

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        return real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"slots": ["Tuesday 10:00"]})
            ),
            **{k: v for k, v in kwargs.items() if k in ("timeout", "follow_redirects")},
        )

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    answer = await tools["http_request"].run({"url": "https://orders.example.com/api/slots"})
    assert answer.startswith("HTTP 200")
    assert "Tuesday 10:00" in answer


# --- send_notification ----------------------------------------------------------


async def test_a_notification_lands_in_the_tray_with_the_agents_words_as_detail(
    stage,
) -> None:
    _, tools, conversation_id, _, db = stage
    answer = await tools["send_notification"].run(
        {"reason": "The customer says the invoice from May is still wrong."}
    )
    assert "They have been told" in answer

    rows = await _tray(db)
    assert [row.message_key for row in rows] == ["agent_notification"]
    assert "invoice from May" in (rows[0].detail or "")
    assert rows[0].action_payload == {"conversation_id": conversation_id}


# --- transfer_call and end_call --------------------------------------------------


async def test_transfer_hands_the_thread_to_a_person_and_asks_one(stage) -> None:
    _, tools, conversation_id, _, db = stage
    answer = await tools["transfer_call"].run({"reason": "They insist on a person."})
    assert "someone will" in answer.lower()

    db.expire_all()
    row = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    assert row.handling == "human"
    rows = await _tray(db)
    assert [r.message_key for r in rows] == ["transfer_requested"]
    assert rows[0].needs_decision is True


async def test_end_call_closes_the_conversation_politely(stage) -> None:
    _, tools, conversation_id, _, db = stage
    answer = await tools["end_call"].run({})
    assert "goodbye" in answer.lower()

    db.expire_all()
    row = await db.scalar(select(Conversation).where(Conversation.id == conversation_id))
    assert row.status == "closed"
    assert row.ended_at is not None

    # And doing it twice is not an error the customer sees.
    again = await tools["transfer_call"].run({})
    assert "already ended" in again


# --- The loop offers what the caller hands it ------------------------------------


async def test_the_loop_offers_the_callers_toolset_and_resolves_against_it() -> None:
    from agent.providers.llm.base import TextDelta, ToolCall
    from agent.reply import reply
    from agent.tools import Tool

    ran: list[dict] = []

    async def remember(arguments: dict) -> str:
        ran.append(arguments)
        return "Noted."

    custom = Tool(
        name="remember",
        description="Test double.",
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
        run=remember,
    )

    offered: list = []

    class ScriptedProvider:
        def __init__(self) -> None:
            self.turn = 0

        async def stream(self, messages, tools):
            offered.append([tool.name for tool in tools])
            self.turn += 1
            if self.turn == 1:
                yield ToolCall(id="1", name="remember", arguments='{"note": "x"}')
            else:
                yield TextDelta(text="Done.")

    spoken = [chunk async for chunk in reply("hi", provider=ScriptedProvider(), tools=[custom])]
    assert "".join(spoken) == "Done."
    assert ran == [{"note": "x"}]
    # The model was offered exactly the caller's set, on every round.
    assert offered == [["remember"], ["remember"]]
