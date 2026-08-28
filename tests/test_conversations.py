"""Reading the transcript archive, and searching it.

The tests that matter are the isolation one and the search one. Everything else on
this screen is a list; those two are where a mistake costs a customer's transcripts or
silently scans a year of them.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Call, Channel, Conversation, Membership, Message, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


async def _thread(
    db: AsyncSession,
    workspace: Workspace,
    channel: Channel,
    lines: list[tuple[str, str]],
    *,
    external_id: str | None = None,
    status: str = "open",
    call: bool = False,
) -> Conversation:
    row = Conversation(
        workspace_id=workspace.id,
        channel_id=channel.id,
        direction="inbound",
        status=status,
        external_id=external_id,
    )
    db.add(row)
    await db.flush()
    for index, (speaker, body) in enumerate(lines):
        db.add(
            Message(
                workspace_id=workspace.id,
                conversation_id=row.id,
                ts_ms=index * 1000,
                speaker=speaker,
                text=body,
            )
        )
    if call:
        db.add(
            Call(
                conversation_id=row.id,
                workspace_id=workspace.id,
                from_e164="+4366412345678",
                recording_path="/var/recordings/secret-path.wav",
                billable_seconds=158,
                provider_cost_micros=2400,
            )
        )
    return row


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces with transcripts, and three roles to read them with."""
    mine = Workspace(name="Wagner & Partner")
    theirs = Workspace(name="Wolf Studio")
    migrated.add_all([mine, theirs])
    await migrated.flush()

    web = Channel(workspace_id=mine.id, kind="web", name="Website")
    phone = Channel(workspace_id=mine.id, kind="phone", name="Main line")
    other = Channel(workspace_id=theirs.id, kind="web", name="Website")
    migrated.add_all([web, phone, other])
    await migrated.flush()

    await _thread(
        migrated,
        mine,
        web,
        [
            ("caller", "Good morning, I would like to move my appointment."),
            ("agent", "Thursday at ten is free, shall I move you to that?"),
            ("caller", "Perfect, Thursday works. Thank you."),
        ],
        external_id="web-1",
    )
    await _thread(
        migrated,
        mine,
        phone,
        [
            ("caller", "I am calling about a refund for the invoice."),
            ("agent", "I will pass that to a colleague."),
        ],
        external_id="+4366412345678",
        status="closed",
        call=True,
    )
    # The other workspace says the same word, which is what makes the isolation test
    # mean something: a leak would be invisible if only one side ever said it.
    await _thread(migrated, theirs, other, [("caller", "A refund, please.")])

    password_hash = hash_password(PASSWORD)
    people = [("sabine", mine, "viewer"), ("wolf", theirs, "owner")]
    for username, workspace, role in people:
        user = User(username=username, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username, _, _ in people:
            http = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        try:
            yield clients
        finally:
            for http in clients.values():
                await http.aclose()


# --- The list ------------------------------------------------------------------


async def test_a_viewer_reads_the_threads(stage) -> None:
    """ "Reads calls. Changes nothing" - the role matrix's own words for a viewer."""
    response = await stage["sabine"].get("/api/conversations")

    assert response.status_code == 200
    body = response.json()
    assert len(body["threads"]) == 2
    assert body["has_more"] is False


async def test_each_thread_carries_its_last_line(stage) -> None:
    """The preview is the list's whole content, and it comes from one query for all
    of them rather than one per row."""
    body = (await stage["sabine"].get("/api/conversations")).json()

    previews = {t["preview"] for t in body["threads"]}
    assert "Perfect, Thursday works. Thank you." in previews
    assert any(t["message_count"] == 3 for t in body["threads"])


async def test_a_call_is_marked_as_one(stage) -> None:
    body = (await stage["sabine"].get("/api/conversations")).json()

    calls = [t for t in body["threads"] if t["is_call"]]
    assert len(calls) == 1
    assert calls[0]["channel"] == "phone"


async def test_filtering_by_channel(stage) -> None:
    body = (await stage["sabine"].get("/api/conversations?channel=phone")).json()

    assert [t["channel"] for t in body["threads"]] == ["phone"]


async def test_filtering_by_status(stage) -> None:
    body = (await stage["sabine"].get("/api/conversations?status=closed")).json()

    assert [t["status"] for t in body["threads"]] == ["closed"]


async def test_an_unknown_status_is_refused(stage) -> None:
    assert (await stage["sabine"].get("/api/conversations?status=whenever")).status_code == 422


# --- Search, which is the query written twice ----------------------------------


async def test_search_finds_the_thread_by_what_was_said_in_it(stage) -> None:
    """Runs through the full-text index that has existed, unused, since the first
    migration - a GIN index on PostgreSQL, an FTS5 table on SQLite."""
    body = (await stage["sabine"].get("/api/conversations?q=refund")).json()

    assert len(body["threads"]) == 1
    assert body["threads"][0]["channel"] == "phone"


async def test_search_matches_nothing_when_nothing_was_said(stage) -> None:
    body = (await stage["sabine"].get("/api/conversations?q=helicopter")).json()

    assert body["threads"] == []


async def test_search_never_reaches_another_workspace(stage) -> None:
    """The other workspace says "refund" too. If the subquery over `messages` were
    unscoped, this is where its transcripts would surface - which is exactly the leak
    D-028 puts `workspace_id` on `messages` to prevent."""
    mine = (await stage["sabine"].get("/api/conversations?q=refund")).json()
    theirs = (await stage["wolf"].get("/api/conversations?q=refund")).json()

    assert len(mine["threads"]) == 1
    assert len(theirs["threads"]) == 1
    assert mine["threads"][0]["id"] != theirs["threads"][0]["id"]


async def test_a_search_for_punctuation_does_not_raise(stage) -> None:
    """A search box takes whatever somebody types.

    Both dialects have a query language, and both raise on some inputs a person can
    reasonably type - a bare quote on SQLite's FTS5, a stray operator on PostgreSQL.
    Whatever this endpoint does with them, it must not be a 500.
    """
    for typed in ('"', "AND", "-", "a OR", "*", "café"):
        response = await stage["sabine"].get("/api/conversations", params={"q": typed})
        assert response.status_code in (200, 422), f"{typed!r} gave {response.status_code}"


# --- One thread ----------------------------------------------------------------


async def test_reading_one_thread_returns_its_lines_in_order(stage) -> None:
    listing = (await stage["sabine"].get("/api/conversations")).json()
    thread_id = next(t["id"] for t in listing["threads"] if t["message_count"] == 3)

    body = (await stage["sabine"].get(f"/api/conversations/{thread_id}")).json()

    assert [m["ts_ms"] for m in body["messages"]] == [0, 1000, 2000]
    assert body["messages"][0]["speaker"] == "caller"


async def test_the_recording_path_never_leaves_the_server(stage) -> None:
    """A filesystem path is the layout of somebody's disk. The screen needs to know
    that audio exists, which is a boolean."""
    listing = (await stage["sabine"].get("/api/conversations")).json()
    call_id = next(t["id"] for t in listing["threads"] if t["is_call"])

    response = await stage["sabine"].get(f"/api/conversations/{call_id}")

    assert response.status_code == 200
    assert "secret-path.wav" not in response.text
    assert "recording_path" not in response.text
    assert response.json()["call"]["has_recording"] is True
    assert response.json()["call"]["billable_seconds"] == 158


async def test_another_workspaces_thread_is_indistinguishable_from_a_missing_one(
    stage,
) -> None:
    """404, not 403. A 403 would confirm the row exists, which is the answer being
    withheld."""
    theirs = (await stage["wolf"].get("/api/conversations")).json()
    foreign_id = theirs["threads"][0]["id"]

    response = await stage["sabine"].get(f"/api/conversations/{foreign_id}")

    assert response.status_code == 404


async def test_signed_out_reads_nothing(stage) -> None:
    stage["sabine"].cookies.clear()

    assert (await stage["sabine"].get("/api/conversations")).status_code == 401


# --- The channel list, and the route order that makes it reachable -------------


async def test_the_channel_list_is_built_from_what_exists(stage) -> None:
    """Chips for channels the workspace has, not for the ten the product commits to.
    A chip that can only ever return nothing is a chip that teaches people not to
    click chips."""
    body = (await stage["sabine"].get("/api/conversations/meta/channels")).json()

    kinds = {c["kind"]: c["thread_count"] for c in body}
    assert kinds == {"web": 1, "phone": 1}


async def test_the_static_path_is_declared_before_the_parameterised_one() -> None:
    """FastAPI matches in declaration order.

    With `/{conversation_id}` first, `/meta/channels` is read as a conversation whose
    id is "meta" and answered with a validation error - and the failure is a 422 on a
    route that looks perfectly correct in isolation.
    """
    from api.routes import conversations as module

    # The router carries its prefix, so these are the full paths.
    paths = [getattr(route, "path", "") for route in module.router.routes]
    assert paths.index("/api/conversations/meta/channels") < paths.index(
        "/api/conversations/{conversation_id}"
    )
