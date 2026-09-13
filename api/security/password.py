"""Argon2id — the one choice that cannot be corrected after the fact.

Every other security decision here can be tightened later. This one cannot: changing
the algorithm means every stored hash is unverifiable, and every user has to reset a
password they did not choose to change. So it is made once, correctly, before a single
account exists.

`argon2-cffi` directly rather than through `passlib`, which `internal/TASKS.md`
suggested: passlib has had no release since 2020, and an unmaintained library is a poor
place for the part of the system that is attacked most.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Length, not character classes.
#
# A rule demanding a symbol and a digit produces `Passw0rd!` — short, predictable, and
# in every cracking dictionary. The sign-in screen already says the right thing:
# "A sentence you can remember beats a short one you cannot."
MINIMUM_LENGTH = 12

# The parameters. Raising any of them later is safe and automatic: `needs_rehash`
# detects a hash made with weaker settings and `verify_and_upgrade` replaces it on the
# next successful sign-in, so the whole installation migrates without anybody resetting
# anything.
#
# time_cost=3, memory_cost=64 MiB, parallelism=4 is the OWASP baseline for Argon2id.
# Memory is the parameter that actually costs an attacker with a GPU; iterations alone
# are cheap to parallelise.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


class PasswordTooShort(ValueError):
    """Raised when a password is below `MINIMUM_LENGTH`."""

    def __init__(self) -> None:
        super().__init__(
            f"A password must be at least {MINIMUM_LENGTH} characters. "
            "A sentence you can remember beats a short one you cannot."
        )


def hash_password(password: str) -> str:
    """Hash a new password, refusing one that is too short."""
    if len(password) < MINIMUM_LENGTH:
        raise PasswordTooShort
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Is this the right password?

    A missing hash returns False rather than raising. An account may legitimately have
    none — a key-only administrator never sets one — and the caller must not be able to
    tell the difference between "no password set" and "wrong password" from the outcome.
    """
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Was this hash made with weaker parameters than the ones in force now?"""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


def verify_and_upgrade(password: str, stored_hash: str | None) -> tuple[bool, str | None]:
    """Verify, and return a fresh hash when the parameters have been raised since.

    Returns `(ok, replacement)`. `replacement` is None when nothing needs storing —
    which is the common case, so the caller writes to the database only when it is not.

    This is what makes raising the parameters a configuration change rather than a
    migration: without it, hashes made in 2026 stay at 2026's cost forever.
    """
    if not verify_password(password, stored_hash):
        return False, None

    assert stored_hash is not None  # noqa: S101 - guaranteed by verify_password above
    if needs_rehash(stored_hash):
        return True, _hasher.hash(password)
    return True, None
