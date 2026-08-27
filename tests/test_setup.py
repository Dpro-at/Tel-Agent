"""First run, and password hashing.

C2's acceptance condition: a fresh database has no user, and the first-run path creates
one. §B9's requirement is the other half of the same sentence — no default credentials,
ever.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Channel, Membership, User, Workspace
from api.security.password import (
    MINIMUM_LENGTH,
    PasswordTooShort,
    hash_password,
    needs_rehash,
    verify_and_upgrade,
    verify_password,
)
from api.setup import AlreadySetUp, create_first_administrator, is_set_up

PASSWORD = "a sentence i can actually remember"  # noqa: S105


# --- D2: password hashing ----------------------------------------------------


def test_a_hash_does_not_contain_the_password() -> None:
    stored = hash_password(PASSWORD)

    assert PASSWORD not in stored
    assert stored.startswith("$argon2id$")


def test_the_same_password_hashes_differently_every_time() -> None:
    """A per-hash salt. Two identical passwords must not produce identical rows."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_the_right_password_verifies_and_the_wrong_one_does_not() -> None:
    stored = hash_password(PASSWORD)

    assert verify_password(PASSWORD, stored) is True
    assert verify_password(PASSWORD + "!", stored) is False


def test_a_short_password_is_refused() -> None:
    """Length, not character classes: a symbol rule produces `Passw0rd!`."""
    with pytest.raises(PasswordTooShort):
        hash_password("x" * (MINIMUM_LENGTH - 1))


def test_an_account_with_no_password_cannot_be_signed_into() -> None:
    """A key-only administrator has no hash, and must not be a way in.

    Returning False rather than raising also means the caller cannot tell "no password
    set" apart from "wrong password" — which is the same reason the reset screen says
    the same thing whether or not an account exists.
    """
    assert verify_password(PASSWORD, None) is False
    assert verify_password(PASSWORD, "") is False


def test_a_corrupt_hash_is_refused_rather_than_raising() -> None:
    """A truncated column must fail closed, not 500."""
    assert verify_password(PASSWORD, "not-a-hash") is False


def test_a_weaker_hash_is_upgraded_on_the_next_sign_in() -> None:
    """Raising the parameters later must not require anybody to reset anything."""
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    assert needs_rehash(weak) is True

    ok, replacement = verify_and_upgrade(PASSWORD, weak)

    assert ok is True
    assert replacement is not None
    assert needs_rehash(replacement) is False


def test_a_current_hash_is_not_rewritten() -> None:
    """The common case writes nothing to the database."""
    stored = hash_password(PASSWORD)

    ok, replacement = verify_and_upgrade(PASSWORD, stored)

    assert ok is True
    assert replacement is None


def test_a_wrong_password_never_returns_a_replacement() -> None:
    weak_but_wrong = hash_password(PASSWORD)

    ok, replacement = verify_and_upgrade("something else entirely", weak_but_wrong)

    assert ok is False
    assert replacement is None


# --- C2: first run -----------------------------------------------------------


async def test_a_fresh_database_has_no_account(migrated: AsyncSession) -> None:
    """§B9: no default credentials. There is nothing to sign in as."""
    assert await is_set_up(migrated) is False
    assert await migrated.scalar(select(func.count()).select_from(User)) == 0


async def test_first_run_creates_the_administrator(migrated: AsyncSession) -> None:
    result = await create_first_administrator(
        migrated,
        username="wagner",
        password=PASSWORD,
        workspace_name="Wagner & Partner",
        email="wagner@example.test",
    )

    assert await is_set_up(migrated) is True
    assert result.user.username == "wagner"
    assert verify_password(PASSWORD, result.user.password_hash) is True
    # The password itself is nowhere in the row.
    assert result.user.password_hash != PASSWORD


async def test_first_run_creates_a_workspace_and_makes_them_its_owner(
    migrated: AsyncSession,
) -> None:
    """An administrator with no workspace can see nothing: every data table is scoped."""
    result = await create_first_administrator(
        migrated,
        username="wagner",
        password=PASSWORD,
        workspace_name="Wagner & Partner",
    )

    membership = await migrated.scalar(
        select(Membership).where(Membership.user_id == result.user.id)
    )
    assert membership is not None
    assert membership.role == "owner"
    assert membership.workspace_id == result.workspace.id


async def test_first_run_creates_the_web_channel(migrated: AsyncSession) -> None:
    """§B5 decision 6's row, created where it can have a workspace to belong to."""
    result = await create_first_administrator(
        migrated,
        username="wagner",
        password=PASSWORD,
        workspace_name="Wagner & Partner",
    )

    channel = await migrated.scalar(
        select(Channel).where(Channel.workspace_id == result.workspace.id)
    )
    assert channel is not None
    assert channel.kind == "web"


async def test_first_run_cannot_happen_twice(migrated: AsyncSession) -> None:
    """Otherwise anybody who can reach the port can add themselves as an owner."""
    await create_first_administrator(
        migrated, username="wagner", password=PASSWORD, workspace_name="Wagner & Partner"
    )

    with pytest.raises(AlreadySetUp):
        await create_first_administrator(
            migrated, username="mallory", password=PASSWORD, workspace_name="Mine Now"
        )

    assert await migrated.scalar(select(func.count()).select_from(User)) == 1


async def test_a_short_password_creates_nothing(migrated: AsyncSession) -> None:
    """The whole first run is one transaction: a rejected password leaves no workspace."""
    with pytest.raises(PasswordTooShort):
        await create_first_administrator(
            migrated,
            username="wagner",
            password="short",  # noqa: S106 - deliberately too short; that is the assertion
            workspace_name="Wagner",
        )

    await migrated.rollback()
    assert await migrated.scalar(select(func.count()).select_from(User)) == 0
    assert await migrated.scalar(select(func.count()).select_from(Workspace)) == 0
