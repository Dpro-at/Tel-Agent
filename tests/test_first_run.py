"""The endpoint that turns an empty database into an installation somebody owns.

`create_first_administrator` and the `/api/setup` entry in `PUBLIC_PATHS` were both
there from the foundations; nothing served the path between them, so a fresh install
had no way in that was not `scripts/seed.py` or a database client. These are about the
route, not the function — `test_setup.py` already owns the transaction.

What can actually hurt somebody here is narrow and specific: a second run that grants
another owner, a short password answered as a crash, and a first run that leaves an
account nobody can sign in as.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import AuthEvent, Channel, Membership, User, Workspace

GOOD_PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def fresh(migrated: AsyncSession, settings: Settings, database_url: str):
    """An installation with nothing in it, and a client with no session."""
    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as http:
            yield http


def _body(name: str = "wagner", password: str = GOOD_PASSWORD) -> dict:
    return {
        "username": name,
        "password": password,
        "workspace_name": "Wagner & Partner",
        "email": "office@wagner-partner.test",
        "locale": "de",
    }


async def test_a_fresh_installation_says_it_needs_setting_up(fresh) -> None:
    """Asked without a session, because the answer decides whether one is possible.

    Without it the sign-in screen sends a first-time operator to a password box that
    no password satisfies, which is where this was discovered.
    """
    answer = await fresh.get("/api/setup")
    assert answer.status_code == 200
    assert answer.json() == {"needed": True}


async def test_the_first_run_creates_an_owner_a_workspace_and_the_web_channel(
    fresh, migrated: AsyncSession
) -> None:
    """All three or none — §B5 decision 6 and D-028. An administrator with no
    workspace can see nothing, and a workspace nobody owns cannot be repaired from
    the interface."""
    answer = await fresh.post("/api/setup", json=_body())
    assert answer.status_code == 201
    assert answer.json()["workspace"] == "Wagner & Partner"

    user = await migrated.scalar(select(User).where(User.username == "wagner"))
    assert user is not None
    assert user.locale == "de"
    membership = await migrated.scalar(select(Membership).where(Membership.user_id == user.id))
    assert membership is not None
    assert membership.role == "owner"
    channel = await migrated.scalar(select(Channel).where(Channel.kind == "web"))
    assert channel is not None
    assert channel.workspace_id == membership.workspace_id


async def test_the_operator_lands_signed_in(fresh) -> None:
    """Somebody who has just chosen a password and is immediately asked for it again
    reasonably wonders whether the first step worked."""
    assert (await fresh.post("/api/setup", json=_body())).status_code == 201

    me = await fresh.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "wagner"
    assert [space["role"] for space in me.json()["workspaces"]] == ["owner"]


async def test_it_says_it_is_no_longer_needed_afterwards(fresh) -> None:
    await fresh.post("/api/setup", json=_body())
    assert (await fresh.get("/api/setup")).json() == {"needed": False}


async def test_a_second_run_cannot_grant_a_second_owner(fresh, migrated: AsyncSession) -> None:
    """The one that matters. This endpoint needs no session, so if it could run twice
    anybody who reaches the port could add themselves as an owner of a live
    installation."""
    assert (await fresh.post("/api/setup", json=_body())).status_code == 201

    again = await fresh.post("/api/setup", json=_body("intruder"))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_set_up"

    assert await migrated.scalar(select(func.count()).select_from(User)) == 1
    assert await migrated.scalar(select(func.count()).select_from(Workspace)) == 1


async def test_a_short_password_is_answered_rather_than_crashed(
    fresh, migrated: AsyncSession
) -> None:
    """§B9 puts the minimum at twelve characters. Answered as a 500, somebody
    concludes the software is broken rather than that their password is short - and
    this is the first thing they ever do with it."""
    answer = await fresh.post("/api/setup", json=_body(password="short"))  # noqa: S106
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "password_too_short"
    assert "12 characters" in answer.json()["error"]["message"]

    # Nothing half-created: the installation is still fresh and they can try again.
    assert await migrated.scalar(select(func.count()).select_from(User)) == 0
    assert (await fresh.get("/api/setup")).json() == {"needed": True}


async def test_a_locale_nothing_renders_is_refused(fresh) -> None:
    """§A4: the interface exists in three. A fourth would leave the very first screen
    in a language the account holder did not choose and cannot change from."""
    answer = await fresh.post("/api/setup", json={**_body(), "locale": "fr"})
    assert answer.status_code == 422


async def test_the_audit_log_opens_with_the_installation_being_created(
    fresh, migrated: AsyncSession
) -> None:
    """An installation whose log does not start with this line was set up some other
    way, which is worth being able to see."""
    await fresh.post("/api/setup", json=_body())

    events = (await migrated.scalars(select(AuthEvent).order_by(AuthEvent.id))).all()
    assert [event.event for event in events][:1] == ["installation_created"]
    assert events[0].username == "wagner"
