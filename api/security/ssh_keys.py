"""Signing in with an SSH key: the challenge, and verifying the signature.

The flow the `login/key` screen describes, unchanged: the server mints a challenge valid
two minutes and usable once, the holder signs it on their own machine with

    ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n tel-agent

and only the signature comes back. No private key ever leaves their machine, and no
password is typed — which is the point on a box where the administrator may never have
set one.

**Verification shells out to `ssh-keygen -Y verify`.** The alternative is parsing the
SSHSIG container and re-implementing signature verification against four key types, and
hand-rolled cryptography is how this kind of thing goes wrong quietly. `ssh-keygen` is
the tool that produced the signature, it is present wherever OpenSSH is, and if it is
missing the failure says so instead of pretending to check.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import KeyChallenge, UserKey

logger = logging.getLogger("api.auth")

# The namespace passed to `ssh-keygen -n`. It scopes a signature to this application:
# a signature made for another namespace does not verify here, so a signature the
# holder produced for some other tool cannot be replayed against Tel-Agent.
NAMESPACE = "tel-agent"

CHALLENGE_LIFETIME = dt.timedelta(minutes=2)
CHALLENGE_PREFIX = "ta1-"

# The signature is a text blob from an untrusted caller. This bounds what gets written
# to a temporary file: an Ed25519 SSHSIG is well under a kilobyte.
MAX_SIGNATURE_BYTES = 16 * 1024


class SshKeygenMissing(RuntimeError):
    """`ssh-keygen` is not on this machine, so signatures cannot be checked."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def ssh_keygen_path() -> str | None:
    return shutil.which("ssh-keygen")


async def mint_challenge(db: DbSession, username: str) -> str:
    """A fresh challenge for one attempt.

    The screen says reloading the page mints a new one, so old rows for this username
    are cleared rather than accumulating.
    """
    await db.execute(delete(KeyChallenge).where(KeyChallenge.username == username))

    challenge = f"{CHALLENGE_PREFIX}{uuid.uuid4()}"
    db.add(
        KeyChallenge(
            challenge=challenge,
            username=username,
            expires_at=_now() + CHALLENGE_LIFETIME,
        )
    )
    await db.commit()
    return challenge


async def take_challenge(db: DbSession, username: str, challenge: str) -> bool:
    """Consume the challenge if it is live. Usable once, as the screen promises."""
    row = await db.scalar(
        select(KeyChallenge).where(
            KeyChallenge.challenge == challenge, KeyChallenge.username == username
        )
    )
    if row is None:
        return False

    expired = _aware(row.expires_at) <= _now()
    # Deleted either way: a challenge that has been offered is spent, and leaving a
    # failed one alive lets an attacker keep trying signatures against it.
    await db.delete(row)
    await db.commit()
    return not expired


def verify_signature(*, message: str, signature: str, public_key: str) -> bool:
    """Does this signature over this message come from the holder of this key?

    Everything is written to files in a directory that is deleted afterwards.
    `ssh-keygen` is invoked as a plain argument list — never through a shell — so
    nothing in the caller's input can be interpreted as a command.
    """
    keygen = ssh_keygen_path()
    if keygen is None:
        raise SshKeygenMissing(
            "ssh-keygen was not found on this machine, so key sign-in cannot verify "
            "anything. Install OpenSSH, or use a password."
        )

    if len(signature.encode("utf-8")) > MAX_SIGNATURE_BYTES:
        return False

    with tempfile.TemporaryDirectory(prefix="telagent-sshsig-") as directory:
        root = Path(directory)
        signature_file = root / "signature"
        allowed = root / "allowed_signers"

        signature_file.write_text(signature, encoding="utf-8")
        # `ssh-keygen -Y verify` checks a signature against an identity in this file.
        # The identity is arbitrary; it only has to match the `-I` argument below.
        allowed.write_text(f"telagent {public_key.strip()}\n", encoding="utf-8")

        try:
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [
                    keygen,
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed),
                    "-I",
                    "telagent",
                    "-n",
                    NAMESPACE,
                    "-s",
                    str(signature_file),
                ],
                input=message.encode("utf-8"),
                capture_output=True,
                # A hung subprocess would hold a request open indefinitely. Verification
                # is arithmetic on a few hundred bytes; five seconds is generous.
                timeout=5,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ssh-keygen verify timed out")
            return False

    return result.returncode == 0


async def find_matching_key(
    db: DbSession, user_id: int, *, message: str, signature: str
) -> UserKey | None:
    """The registered key that produced this signature, if any.

    Every key on the account is tried, because the caller does not say which one they
    used — and asking them to would be one more thing to get wrong for no gain.
    """
    keys = (await db.execute(select(UserKey).where(UserKey.user_id == user_id))).scalars().all()

    for key in keys:
        # `ssh-keygen` is a subprocess. Running it on the event loop would stall every
        # other request for as long as it takes, so only that call goes to a thread -
        # the database work around it stays where it belongs.
        matches = await asyncio.to_thread(
            verify_signature,
            message=message,
            signature=signature,
            public_key=key.public_key,
        )
        if matches:
            key.last_used_at = _now()
            await db.commit()
            return key
    return None


async def delete_expired_challenges(db: DbSession) -> int:
    result = await db.execute(delete(KeyChallenge).where(KeyChallenge.expires_at <= _now()))
    await db.commit()
    return result.rowcount or 0
