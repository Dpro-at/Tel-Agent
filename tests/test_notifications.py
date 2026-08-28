"""Notifications — P4.

The screen makes one distinction that everything here turns on: *"Two kinds of thing
live here. The ones at the top are waiting on a decision."* A test suite that treats
this as read/unread would pass while the product quietly filed away work nobody did.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api import notifications
from api.config import Settings
from api.main import create_app
from api.models import Membership, Notification, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """Two workspaces; a reception account and a viewer in the first, plus items."""
    first = Workspace(name="Wagner & Partner")
    second = Workspace(name="Wolf Studio")
    migrated.add_all([first, second])
    await migrated.flush()

    password_hash = hash_password(PASSWORD)
    people = {}
    for username, role in (("sabine", "reception"), ("lukas", "viewer")):
        user = User(username=username, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        people[username] = user
        migrated.add(Membership(user_id=user.id, workspace_id=first.id, role=role))

    # Somebody who only belongs to the other workspace.
    outsider = User(username="wolf", password_hash=password_hash)
    migrated.add(outsider)
    await migrated.flush()
    migrated.add(Membership(user_id=outsider.id, workspace_id=second.id, role="owner"))
    await migrated.commit()

    # One decision and one log entry in the first workspace; one decision in the other.
    await notifications.raise_notification(
        migrated,
        workspace_id=first.id,
        category="failure",
        message_key="backup_failed",
        detail="connection refused to nas.wagner-partner.local:445",
        needs_decision=True,
        primary_action="resend_notification",
        action_payload={"to": "+4366412345678"},
    )
    await notifications.raise_notification(
        migrated,
        workspace_id=first.id,
        category="system",
        message_key="task_failed",
        params={"task": "cleanup_sessions"},
        needs_decision=False,
    )
    await notifications.raise_notification(
        migrated,
        workspace_id=second.id,
        category="review",
        message_key="backup_no_target",
        needs_decision=True,
    )

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("sabine", "lukas", "wolf"):
            client = AsyncClient(transport=transport, base_url="http://localhost")
            response = await client.post(
                "/api/auth/login", json={"username": username, "password": PASSWORD}
            )
            assert response.status_code == 200
            clients[username] = client
        try:
            yield (first.id, second.id), clients
        finally:
            for client in clients.values():
                await client.aclose()


# --- The two sections --------------------------------------------------------


async def test_decisions_and_log_arrive_separated(stage) -> None:
    """The split is the product's, not the layout's: one list is work, one is history."""
    _ids, clients = stage

    body = (await clients["sabine"].get("/api/notifications")).json()

    assert [item["message_key"] for item in body["waiting"]] == ["backup_failed"]
    # And the parameters travel with it, because the sentence is assembled on the
    # screen and half a sentence is worse than none.
    assert body["waiting"][0]["detail"] == "connection refused to nas.wagner-partner.local:445"
    assert [item["message_key"] for item in body["log"]] == ["task_failed"]
    assert body["open_count"] == 1


async def test_the_action_and_its_payload_survive(stage) -> None:
    """The screen's primary button needs to know what to do and to what."""
    _ids, clients = stage

    waiting = (await clients["sabine"].get("/api/notifications")).json()["waiting"][0]

    assert waiting["primary_action"] == "resend_notification"
    assert waiting["action_payload"] == {"to": "+4366412345678"}


async def test_filtering_by_category(stage) -> None:
    _ids, clients = stage

    body = (await clients["sabine"].get("/api/notifications?category=system")).json()

    assert body["waiting"] == []
    assert len(body["log"]) == 1


# --- Mark all as read, and what it must not do -------------------------------


async def test_mark_all_read_clears_the_log_and_keeps_the_decision(stage) -> None:
    """The heart of P4.

    A version that resolved everything would file away a promised SMS that never went
    out, without anybody deciding what to do about it - which is the one thing this
    screen exists to prevent.
    """
    _ids, clients = stage

    marked = (await clients["sabine"].post("/api/notifications/mark-log-read")).json()

    assert marked == {"resolved": 1, "still_waiting": 1}

    after = (await clients["sabine"].get("/api/notifications")).json()
    assert len(after["waiting"]) == 1, "the decision must survive being marked read"
    assert after["open_count"] == 1


async def test_resolving_moves_an_item_out_of_waiting(stage) -> None:
    _ids, clients = stage

    waiting = (await clients["sabine"].get("/api/notifications")).json()["waiting"][0]
    resolved = await clients["sabine"].post(f"/api/notifications/{waiting['id']}/resolve")

    assert resolved.status_code == 200
    assert resolved.json()["resolved_at"] is not None

    after = (await clients["sabine"].get("/api/notifications")).json()
    assert after["waiting"] == []
    assert after["open_count"] == 0


async def test_resolving_twice_keeps_the_first_timestamp(stage, migrated: AsyncSession) -> None:
    """Two people clicking the same button must not rewrite when it was handled."""
    _ids, clients = stage
    waiting = (await clients["sabine"].get("/api/notifications")).json()["waiting"][0]

    first = (await clients["sabine"].post(f"/api/notifications/{waiting['id']}/resolve")).json()
    second = (
        await clients["sabine"].post(f"/api/notifications/{waiting['id']}/resolve")
    ).json()

    assert first["resolved_at"] == second["resolved_at"]


# --- Scoping and roles -------------------------------------------------------


async def test_one_workspace_never_sees_another(stage) -> None:
    """D-028 at the one place that reads this table."""
    _ids, clients = stage

    mine = (await clients["sabine"].get("/api/notifications")).json()
    theirs = (await clients["wolf"].get("/api/notifications")).json()

    keys = {item["message_key"] for item in mine["waiting"] + mine["log"]}
    assert "backup_no_target" not in keys
    assert [item["message_key"] for item in theirs["waiting"]] == ["backup_no_target"]


async def test_a_foreign_id_is_indistinguishable_from_a_missing_one(
    stage, migrated: AsyncSession
) -> None:
    """Resolving somebody else's item must not reveal that it exists."""
    from sqlalchemy import select

    (_first, second_id), clients = stage
    other = await migrated.scalar(
        select(Notification).where(Notification.workspace_id == second_id)
    )
    assert other is not None

    foreign = await clients["sabine"].post(f"/api/notifications/{other.id}/resolve")
    missing = await clients["sabine"].post("/api/notifications/999999/resolve")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_a_viewer_reads_but_cannot_resolve(stage) -> None:
    """ "Reads calls. Changes nothing, answers nothing." - the role matrix, enforced."""
    _ids, clients = stage

    assert (await clients["lukas"].get("/api/notifications")).status_code == 200

    waiting = (await clients["lukas"].get("/api/notifications")).json()["waiting"][0]
    refused = await clients["lukas"].post(f"/api/notifications/{waiting['id']}/resolve")

    assert refused.status_code == 403
    assert "reception" in refused.json()["error"]["message"]
    assert (await clients["lukas"].post("/api/notifications/mark-log-read")).status_code == 403


async def test_signing_out_closes_the_list(stage) -> None:
    """Closed by default, like everything else."""
    _ids, clients = stage
    clients["sabine"].cookies.clear()

    assert (await clients["sabine"].get("/api/notifications")).status_code == 401


# --- Raising ------------------------------------------------------------------


async def test_raising_never_raises(migrated: AsyncSession) -> None:
    """A notification about a failure must not fail in a way that loses the failure."""
    result = await notifications.raise_notification(
        migrated,
        workspace_id=999999,  # no such workspace: the insert violates a foreign key
        category="failure",
        message_key="backup_no_target",
    )

    assert result is None  # reported, not raised


async def test_an_unknown_category_is_caught_at_the_call_site(
    migrated: AsyncSession,
) -> None:
    workspace = Workspace(name="W")
    migrated.add(workspace)
    await migrated.flush()

    with pytest.raises(AssertionError):
        await notifications.raise_notification(
            migrated,
            workspace_id=workspace.id,
            category="gossip",
            message_key="backup_no_target",
        )


# --- The catalogue and the translations must not drift apart ------------------


def _locale_messages(locale: str) -> dict[str, str]:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "locales" / locale / "notifications.json").read_text(encoding="utf-8")
    return json.loads(text)


def test_every_declared_message_has_a_string_in_every_language() -> None:
    """A key with no sentence behind it is a screen showing a raw identifier.

    The locale gate checks that `de` and `ar` match `en`; nothing checked that `en`
    matches the *code*. This is that half.
    """
    from api.notifications import MESSAGES

    for locale in ("en", "de", "ar"):
        strings = _locale_messages(locale)
        missing = [key for key in MESSAGES if f"msg_{key}" not in strings]
        assert not missing, f"{locale} is missing: {missing}"


def test_the_declared_parameters_match_the_placeholders() -> None:
    """The catalogue says `backup_failed` needs `reason`; the sentence has `{reason}`.

    Drift either way is a bug with no symptom until somebody reads the screen: a
    placeholder with no declared parameter prints as `{reason}`, and a declared
    parameter with no placeholder is a value silently dropped.
    """
    import re

    from api.notifications import MESSAGES

    strings = _locale_messages("en")
    for key, declared in MESSAGES.items():
        placeholders = set(re.findall(r"\{(\w+)\}", strings[f"msg_{key}"]))
        assert placeholders == set(declared), (
            f"{key}: sentence has {sorted(placeholders)}, catalogue declares {sorted(declared)}"
        )


async def test_an_unknown_message_is_refused(migrated: AsyncSession) -> None:
    """Raised rather than swallowed.

    Everything else in `raise_notification` is written defensively, because a
    notification must not break what it reports on. An unknown key is different: it is
    a mistake in the *caller*, and swallowing it would leave a row nobody can read and
    no clue where it came from.
    """
    from api.notifications import UnknownMessage, raise_notification

    with pytest.raises(UnknownMessage, match="unknown notification message"):
        await raise_notification(
            migrated, workspace_id=1, category="system", message_key="no_such_message"
        )


async def test_a_missing_parameter_is_refused(migrated: AsyncSession) -> None:
    """Caught at the call site, not printed as `{reason}` onto somebody's screen."""
    from api.notifications import UnknownMessage, raise_notification

    with pytest.raises(UnknownMessage, match="needs"):
        # `task_failed` declares `task`, and this call does not pass it.
        await raise_notification(
            migrated, workspace_id=1, category="system", message_key="task_failed"
        )


# --- `detail`: the machine's words, and what must not be in them --------------


async def test_a_secret_in_the_detail_is_redacted_before_it_is_stored(
    migrated: AsyncSession,
) -> None:
    """`detail` is nearly always `str(exception)`, and that shape has leaked before.

    A SQLAlchemy parameter dump inside a failed INSERT carried a live password into a
    log line in this codebase. A notification is the worse place for it to land: kept
    for thirty days, and readable by anybody with `viewer` rather than only by whoever
    can reach the log.
    """
    from api.notifications import raise_notification

    workspace = Workspace(name="W")
    migrated.add(workspace)
    await migrated.flush()

    leaked = "hunter2-the-real-one"
    row = await raise_notification(
        migrated,
        workspace_id=workspace.id,
        category="failure",
        message_key="mail_failed",
        detail=f"login refused [parameters: {{'smtp_password': '{leaked}'}}]",
    )

    assert row is not None
    assert leaked not in (row.detail or "")
    assert "[redacted]" in (row.detail or "")


async def test_a_long_detail_is_trimmed(migrated: AsyncSession) -> None:
    """Past a point it stops being a hint and becomes a log.

    The whole text is in the log under the request id, where somebody who needs all of
    it can find it; a notification is a sentence and a clue, not a transcript.
    """
    from api.notifications import DETAIL_LIMIT, raise_notification

    workspace = Workspace(name="W")
    migrated.add(workspace)
    await migrated.flush()

    row = await raise_notification(
        migrated,
        workspace_id=workspace.id,
        category="failure",
        message_key="backup_failed",
        detail="x" * (DETAIL_LIMIT * 3),
    )

    assert row is not None
    assert len(row.detail or "") == DETAIL_LIMIT


async def test_no_parameter_carries_prose() -> None:
    """The rule this column pair exists to enforce.

    A parameter is a path, a count, a name or a date - something that reads the same in
    every language. The moment one carries an explanatory sentence, the server's
    language is back inside a translated one, which is the failure the whole change was
    made to fix. `reason` and `subject` were exactly that, and they are now `detail`.
    """
    from api.notifications import MESSAGES

    prose = {"reason", "message", "error", "subject", "description", "text", "body"}
    for key, params in MESSAGES.items():
        assert not (params & prose), f"{key} takes prose as a parameter: {params & prose}"
