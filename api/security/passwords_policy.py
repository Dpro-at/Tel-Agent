"""Changing a password: the policy, the history, and what it invalidates.

Three promises the `new-password` screen makes, kept here rather than in each caller:

* *"At least N characters"* — enforced by `hash_password`.
* *"This is one of your last five passwords... choose one you have not used here before."*
* *"Every other browser and phone signed in to this account is signed out."*

The last one is not a courtesy. A password is usually changed because somebody fears it
is known, and a change that leaves the other sessions alive changes nothing for the
person who already has one.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import PasswordHistory, Session, User
from api.security.password import hash_password, verify_password

logger = logging.getLogger("api.auth")

# "This is one of your last five passwords" — the number the screen states.
HISTORY_DEPTH = 5


class PasswordReused(ValueError):
    """The new password is one of the recent ones."""

    def __init__(self) -> None:
        super().__init__(
            f"This is one of your last {HISTORY_DEPTH} passwords. "
            "Choose one you have not used here before."
        )


async def _recent(db: DbSession, user_id: int) -> list[PasswordHistory]:
    rows = await db.execute(
        select(PasswordHistory)
        .where(PasswordHistory.user_id == user_id)
        # By id, not by created_at. The timestamp has one-second resolution on SQLite,
        # so several changes in one second tie - and a tie here once meant the trim
        # deleted the *newest* fingerprints and kept the oldest, which let a password
        # from years ago back in while refusing recent ones. The id is monotonic.
        .order_by(PasswordHistory.id.desc())
        .limit(HISTORY_DEPTH)
    )
    return list(rows.scalars())


async def set_password(
    db: DbSession,
    user: User,
    new_password: str,
    *,
    keep_session_token_hash: str | None = None,
) -> int:
    """Apply a new password. Returns how many sessions were ended.

    The current password is *not* checked here — who is allowed to call this is the
    caller's question, and it has two legitimate answers: somebody who typed their old
    password, and somebody who proved themselves with a code or a key. Putting the check
    here would mean the reset flow had to lie about knowing the old one.
    """
    for entry in await _recent(db, user.id):
        if verify_password(new_password, entry.password_hash):
            raise PasswordReused

    # Raises `PasswordTooShort` before anything is written.
    fresh = hash_password(new_password)

    user.password_hash = fresh
    db.add(PasswordHistory(user_id=user.id, password_hash=fresh))

    # Trim beyond the depth: this table would otherwise grow forever, and a fingerprint
    # of a password from four years ago protects nothing.
    keep = {entry.id for entry in await _recent(db, user.id)}
    await db.execute(
        delete(PasswordHistory).where(
            PasswordHistory.user_id == user.id,
            PasswordHistory.id.notin_(keep) if keep else True,
        )
    )

    condition = Session.user_id == user.id
    if keep_session_token_hash:
        condition = condition & (Session.token_hash != keep_session_token_hash)
    result = await db.execute(delete(Session).where(condition))

    await db.commit()
    ended = result.rowcount or 0
    logger.info("password changed", extra={"user_id": user.id, "sessions_ended": ended})
    return ended
