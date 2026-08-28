"""Invitations - D-034, both halves.

The admin half issues a one-time link and can rotate it; the public half turns the
link into an account with a self-chosen name, exactly once. The properties that
matter: the token is stored only as a hash, rotation kills the old link, expiry and
reuse are told apart honestly, and acceptance flips the membership and signs in.
"""

from __future__ import annotations

import datetime as dt

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Invite, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """One workspace, one admin signed in, and an anonymous client for the links."""
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()
    admin = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(admin)
    await migrated.flush()
    migrated.add(Membership(user_id=admin.id, workspace_id=workspace.id, role="admin"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        signed_in = AsyncClient(transport=transport, base_url="http://localhost")
        anonymous = AsyncClient(transport=transport, base_url="http://localhost")
        response = await signed_in.post(
            "/api/auth/login", json={"username": "mohamed", "password": PASSWORD}
        )
        assert response.status_code == 200
        try:
            yield signed_in, anonymous
        finally:
            await signed_in.aclose()
            await anonymous.aclose()


async def _invite(client: AsyncClient, email: str = "julia@example.test") -> dict:
    response = await client.post(
        "/api/members/invites", json={"email": email, "role": "reception"}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- Issuing ------------------------------------------------------------------


async def test_an_invite_creates_the_row_the_list_shows(stage, migrated: AsyncSession) -> None:
    """identity.py's commitment: an invited person is a membership state in the same
    list as everybody else, so the row exists before anybody accepts."""
    signed_in, _ = stage

    body = await _invite(signed_in)

    assert body["username"] == "julia"
    assert body["role"] == "reception"
    assert body["invite_path"].startswith("/invite/")
    members = (await signed_in.get("/api/members")).json()
    assert [m["role"] for m in members if m["username"] == "julia"] == ["invited"]


async def test_the_token_is_stored_only_as_a_hash(stage, migrated: AsyncSession) -> None:
    """A database dump must not be a bag of working invite links."""
    signed_in, _ = stage

    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    migrated.expire_all()
    row = await migrated.scalar(select(Invite))
    assert row is not None
    assert token not in row.token_hash
    assert len(row.token_hash) == 64  # sha256 hex, same discipline as sessions


async def test_a_second_julia_gets_a_distinct_provisional_name(stage) -> None:
    signed_in, _ = stage

    first = await _invite(signed_in, "julia@example.test")
    second = await _invite(signed_in, "julia@elsewhere.test")

    assert first["username"] == "julia"
    assert second["username"] == "julia2"


async def test_only_an_admin_invites(stage, migrated: AsyncSession) -> None:
    _signed_in, anonymous = stage

    refused = await anonymous.post(
        "/api/members/invites", json={"email": "x@example.test", "role": "viewer"}
    )

    assert refused.status_code == 401


async def test_an_invitation_never_grants_ownership(stage) -> None:
    signed_in, _ = stage

    response = await signed_in.post(
        "/api/members/invites", json={"email": "x@example.test", "role": "owner"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_role"


# --- The link, read anonymously -----------------------------------------------


async def test_the_link_says_what_it_opens(stage) -> None:
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    preview = (await anonymous.get(f"/api/invites/{token}")).json()

    assert preview["workspace"] == "Wagner & Partner"
    assert preview["role"] == "reception"
    assert preview["suggested_username"] == "julia"


async def test_an_unknown_token_is_not_valid(stage) -> None:
    _, anonymous = stage

    response = await anonymous.get("/api/invites/not-a-real-token")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "invite_invalid"


async def test_an_expired_link_says_so_honestly(stage, migrated: AsyncSession) -> None:
    """The person holding a dead link needs to know to ask for a new one."""
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    migrated.expire_all()
    row = await migrated.scalar(select(Invite))
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    await migrated.commit()

    response = await anonymous.get(f"/api/invites/{token}")

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "invite_expired"


async def test_rotation_kills_the_old_link(stage, migrated: AsyncSession) -> None:
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    old_token = body["invite_path"].removeprefix("/invite/")

    fresh = await signed_in.post(f"/api/members/{body['user_id']}/invite-link")
    assert fresh.status_code == 200
    new_token = fresh.json()["invite_path"].removeprefix("/invite/")

    assert (await anonymous.get(f"/api/invites/{old_token}")).status_code == 404
    assert (await anonymous.get(f"/api/invites/{new_token}")).status_code == 200


# --- Accepting ----------------------------------------------------------------


async def test_accepting_names_the_person_grants_the_role_and_signs_in(
    stage, migrated: AsyncSession
) -> None:
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    accepted = await anonymous.post(
        f"/api/invites/{token}/accept",
        json={"username": "julia.w", "password": "a sentence julia will remember"},
    )

    assert accepted.status_code == 200, accepted.text
    me = accepted.json()
    assert me["username"] == "julia.w"
    assert me["workspaces"] == [
        {"id": me["workspaces"][0]["id"], "name": "Wagner & Partner", "role": "reception"}
    ]
    # Signed in: the cookie the response set reaches a protected route.
    assert (await anonymous.get("/api/auth/me")).status_code == 200

    # And the admin's list agrees.
    members = (await signed_in.get("/api/members")).json()
    assert [m["role"] for m in members if m["username"] == "julia.w"] == ["reception"]


async def test_a_link_works_exactly_once(stage) -> None:
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    first = await anonymous.post(
        f"/api/invites/{token}/accept",
        json={"username": "julia.w", "password": "a sentence julia will remember"},
    )
    assert first.status_code == 200

    again = await anonymous.post(
        f"/api/invites/{token}/accept",
        json={"username": "somebody.else", "password": "a different long sentence"},
    )
    assert again.status_code == 410
    assert again.json()["error"]["code"] == "invite_used"


async def test_a_taken_username_is_refused(stage) -> None:
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    response = await anonymous.post(
        f"/api/invites/{token}/accept",
        json={"username": "mohamed", "password": "a sentence julia will remember"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "username_taken"


async def test_the_password_policy_applies_here_too(stage) -> None:
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    response = await anonymous.post(
        f"/api/invites/{token}/accept", json={"username": "julia.w", "password": "short"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "password_too_short"


async def test_cancelling_the_invite_kills_the_link_with_it(
    stage, migrated: AsyncSession
) -> None:
    """DELETE on the invited row is "Cancel invite". A membership that is gone while
    the token still answers would be an invitation nobody sent - both die."""
    signed_in, anonymous = stage
    body = await _invite(signed_in)
    token = body["invite_path"].removeprefix("/invite/")

    cancelled = await signed_in.delete(f"/api/members/{body['user_id']}")
    assert cancelled.status_code == 204

    assert (await anonymous.get(f"/api/invites/{token}")).status_code == 404
    accept = await anonymous.post(
        f"/api/invites/{token}/accept",
        json={"username": "julia.w", "password": "a sentence julia will remember"},
    )
    assert accept.status_code == 404
