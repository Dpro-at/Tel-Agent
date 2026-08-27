"""Six-digit codes: issuing, delivering and checking them.

One mechanism, two purposes (D-030). The reset flow sends one, and the second factor
asks for one — the `login/code` screen serves both, and its comment says so: *"The same
screen serves two-factor sign-in. The only difference is where the caller came from and
where they go next."*

**A code issued for one purpose is never accepted for the other.** Without that, asking
for a password reset would hand somebody a code that satisfies two-factor sign-in, and
the second factor would be bypassable by anybody who can read the account's email.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import AuthCode, User

logger = logging.getLogger("api.auth")

CODE_LENGTH = 6
# Ten minutes and three attempts, as the screen states.
CODE_LIFETIME = dt.timedelta(minutes=10)
MAX_ATTEMPTS = 3


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_code() -> str:
    """Six digits from the system CSPRNG, leading zeros kept.

    `secrets.randbelow` rather than `random`: the latter is seeded predictably and its
    output can be reconstructed from a few observed values, which for a code that
    authorises a password reset is the whole ballgame.
    """
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"


async def issue(db: DbSession, user: User, purpose: str) -> str:
    """Replace any outstanding code for this purpose and return the new one.

    Replaced rather than added: two live codes double the guessing surface, and the
    screen's "ask for a new one - the old one will not work even if you find it"
    promises exactly this.
    """
    await db.execute(
        delete(AuthCode).where(AuthCode.user_id == user.id, AuthCode.purpose == purpose)
    )

    code = generate_code()
    db.add(
        AuthCode(
            user_id=user.id,
            purpose=purpose,
            code_hash=hash_code(code),
            expires_at=_now() + CODE_LIFETIME,
            attempts_left=MAX_ATTEMPTS,
        )
    )
    await db.commit()
    # The code itself is returned to the caller to deliver. It is never logged.
    logger.info("code issued", extra={"user_id": user.id, "purpose": purpose})
    return code


class CodeResult:
    """Why a code was refused, so the screen can say the right thing."""

    OK = "ok"
    WRONG = "wrong"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"


async def verify(db: DbSession, user: User, purpose: str, code: str) -> tuple[str, int]:
    """Check a code and consume it on success. Returns `(result, attempts_left)`.

    A successful code is deleted immediately. Leaving it usable for the rest of its ten
    minutes means a code read over somebody's shoulder still works after they have used
    it.
    """
    row = await db.scalar(
        select(AuthCode).where(AuthCode.user_id == user.id, AuthCode.purpose == purpose)
    )
    if row is None:
        return CodeResult.EXPIRED, 0

    if _aware(row.expires_at) <= _now():
        await db.delete(row)
        await db.commit()
        return CodeResult.EXPIRED, 0

    # Constant-time: a length-or-prefix comparison on the hash would leak how much of a
    # guess was right, and six digits do not have much margin to spare.
    if secrets.compare_digest(row.code_hash, hash_code(code)):
        await db.delete(row)
        await db.commit()
        return CodeResult.OK, 0

    row.attempts_left -= 1
    remaining = row.attempts_left
    if remaining <= 0:
        await db.delete(row)
        await db.commit()
        return CodeResult.EXHAUSTED, 0

    await db.commit()
    return CodeResult.WRONG, remaining


async def delete_expired_codes(db: DbSession) -> int:
    result = await db.execute(delete(AuthCode).where(AuthCode.expires_at <= _now()))
    await db.commit()
    return result.rowcount or 0
