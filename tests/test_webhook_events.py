"""The events the product actually raises, and the settings screen's "Send test".

`WEBHOOK_EVENTS` has offered five names since the registry was written, and the
channels have been raising two of them. These are about the other three - a
subscriber to `assistant.changed`, `knowledge.changed` or `conversation.ended`
hearing something rather than silence - and about the one delivery an operator can
fire by hand to prove their receiver is wired before anything real depends on it.

The send-test route is exercised against a captured request, and the assertion that
matters is the same one `test_webhook_delivery.py` closes with: the signature over
the bytes that were delivered verifies with the documented recipe.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import (
    BackgroundJob,
    Channel,
    Conversation,
    Membership,
    User,
    Webhook,
    Workspace,
)
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
SECRET = "the-shared-secret-a-receiver-also-has"  # noqa: S105
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch):
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
    """A workspace with an admin signed in, and a hook subscribed to everything."""
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()

    hook = Webhook(
        workspace_id=workspace.id,
        url="https://wagner-partner.test/hooks/tel-agent",
        events=["assistant.changed", "knowledge.changed", "conversation.ended"],
        secret=SECRET,
    )
    migrated.add(hook)

    user = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
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
        try:
            yield http, workspace.id, hook.id, migrated, app
        finally:
            await http.aclose()


async def _webhook_jobs(db: AsyncSession) -> list[BackgroundJob]:
    db.expire_all()
    rows = (
        (
            await db.execute(
                select(BackgroundJob)
                .where(BackgroundJob.kind == "webhook")
                .order_by(BackgroundJob.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


# --- assistant.changed ------------------------------------------------------------


async def test_an_assistants_whole_life_is_told_to_the_hook(stage) -> None:
    http, _, _, db, _ = stage

    made = await http.post("/api/assistants", json={"name": "Reception"})
    assert made.status_code == 201, made.text
    assistant_id = made.json()["id"]
    assert (
        await http.patch(f"/api/assistants/{assistant_id}", json={"role": "Front desk"})
    ).status_code == 200
    assert (await http.delete(f"/api/assistants/{assistant_id}")).status_code == 204

    jobs = await _webhook_jobs(db)
    assert [job.payload["event"] for job in jobs] == ["assistant.changed"] * 3
    assert [job.payload["data"]["action"] for job in jobs] == ["added", "changed", "removed"]
    assert jobs[0].payload["data"]["name"] == "Reception"


# --- knowledge.changed ------------------------------------------------------------


async def test_adding_and_removing_knowledge_are_both_told(stage) -> None:
    http, _, _, db, _ = stage

    made = await http.post(
        "/api/knowledge", json={"title": "Opening hours", "content": "Until noon."}
    )
    assert made.status_code == 201, made.text
    knowledge_id = made.json()["id"]
    assert (await http.delete(f"/api/knowledge/{knowledge_id}")).status_code == 204

    jobs = await _webhook_jobs(db)
    assert [job.payload["event"] for job in jobs] == ["knowledge.changed"] * 2
    assert [job.payload["data"]["action"] for job in jobs] == ["added", "removed"]
    assert jobs[0].payload["data"]["title"] == "Opening hours"
    # Each delivery carries its job's id, so the receiver's dedup key means something:
    # distinct events get distinct ids, retries of one delivery repeat the same one.
    assert [job.payload["delivery_id"] for job in jobs] == [job.id for job in jobs]


# --- conversation.ended -----------------------------------------------------------


async def test_the_agent_closing_a_conversation_is_told_to_the_hook(stage) -> None:
    """`end_call` is currently the one writer of `status='closed'`, so the event rides
    inside the tool - the same session that closes the row queues the delivery."""
    from api.agent_tools import toolset

    _, workspace_id, _, db, app = stage

    channel = Channel(workspace_id=workspace_id, kind="web", name="Web", status="active")
    db.add(channel)
    await db.flush()
    conversation = Conversation(
        workspace_id=workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id="visitor-9",
        handling="ai",
        status="open",
    )
    db.add(conversation)
    await db.commit()

    tools = {
        tool.name: tool
        for tool in toolset(
            app.state.sessionmaker,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
        )
    }
    answer = await tools["end_call"].run({})
    assert "goodbye" in answer.lower()

    jobs = await _webhook_jobs(db)
    assert [job.payload["event"] for job in jobs] == ["conversation.ended"]
    assert jobs[0].payload["data"]["conversation"] == "visitor-9"
    assert jobs[0].payload["data"]["ended_at"] is not None


# --- Send test --------------------------------------------------------------------


async def test_send_test_delivers_a_signed_post_the_recipe_verifies(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _, hook_id, _, _ = stage

    caught: list[httpx.Request] = []

    def fake_client(*args, **kwargs):
        def answer(request: httpx.Request) -> httpx.Response:
            caught.append(request)
            return httpx.Response(204)

        return AsyncClient(transport=httpx.MockTransport(answer))

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    answered = await http.post(f"/api/webhooks/{hook_id}/test")
    assert answered.status_code == 200, answered.text
    assert answered.json() == {"delivered": True, "status_code": 204, "error": None}

    (request,) = caught
    body = request.content
    timestamp = int(request.headers["X-Tel-Agent-Timestamp"])
    expected = (
        "sha256="
        + hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    )
    assert request.headers["X-Tel-Agent-Signature"] == expected
    assert request.headers["X-Tel-Agent-Event"] == "webhook.test"
    assert json.loads(body)["event"] == "webhook.test"


async def test_send_test_reports_a_failure_instead_of_raising(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The button exists to find a broken receiver, so a broken receiver is a result
    to show, not an exception to swallow into a 500."""
    http, _, hook_id, _, _ = stage

    def fake_client(*args, **kwargs):
        return AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    answered = await http.post(f"/api/webhooks/{hook_id}/test")
    assert answered.status_code == 200, answered.text
    result = answered.json()
    assert result["delivered"] is False
    assert result["status_code"] == 500


async def test_send_test_on_a_foreign_hook_is_a_plain_404(stage) -> None:
    http, _, _, _, _ = stage
    answered = await http.post("/api/webhooks/9999/test")
    assert answered.status_code == 404
