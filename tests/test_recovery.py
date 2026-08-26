"""The ways back in: the six-digit code, the SSH key, and the new password.

D9's acceptance condition — the credential works once and is refused the second time —
is asserted for both single-use artefacts here: the code and the challenge.

The SSH tests sign with a real key using the real `ssh-keygen`, because a mocked
verifier proves the mock. The suite skips them only where OpenSSH is absent, and CI has
it everywhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import AuthCode, Session, User, UserKey
from api.security import codes, ssh_keys
from api.security.password import verify_password
from api.security.passwords_policy import HISTORY_DEPTH, PasswordReused, set_password
from api.security.session import COOKIE_NAME
from api.setup import create_first_administrator

USERNAME = "wagner"
PASSWORD = "a sentence i can actually remember"  # noqa: S105
NEW_PASSWORD = "an entirely different sentence"  # noqa: S105

needs_ssh_keygen = pytest.mark.skipif(
    ssh_keys.ssh_keygen_path() is None, reason="OpenSSH is not installed"
)


@pytest.fixture
async def user(migrated: AsyncSession) -> User:
    """The administrator, for tests that talk to the database directly."""
    result = await create_first_administrator(
        migrated,
        username=USERNAME,
        password=PASSWORD,
        workspace_name="Wagner & Partner",
        email="wagner@example.test",
    )
    return result.user


@pytest.fixture
async def signed_up(migrated: AsyncSession, settings: Settings, database_url: str):
    await create_first_administrator(
        migrated,
        username=USERNAME,
        password=PASSWORD,
        workspace_name="Wagner & Partner",
        email="wagner@example.test",
    )
    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield client


# --- The six-digit code ------------------------------------------------------


async def test_a_code_verifies_once_and_never_twice(migrated: AsyncSession, user: User) -> None:
    """D9's acceptance shape: works the first time, refused the second."""
    code = await codes.issue(migrated, user, "reset")

    first, _ = await codes.verify(migrated, user, "reset", code)
    second, _ = await codes.verify(migrated, user, "reset", code)

    assert first == codes.CodeResult.OK
    assert second == codes.CodeResult.EXPIRED


async def test_a_reset_code_is_not_a_second_factor(migrated: AsyncSession, user: User) -> None:
    """The purposes must not be interchangeable, or the reset flow bypasses 2FA."""
    code = await codes.issue(migrated, user, "reset")

    result, _ = await codes.verify(migrated, user, "second_factor", code)

    assert result != codes.CodeResult.OK


async def test_three_wrong_guesses_kill_the_code(migrated: AsyncSession, user: User) -> None:
    """The screen counts down from three, and the third is final."""
    await codes.issue(migrated, user, "reset")

    results = [(await codes.verify(migrated, user, "reset", "000000"))[0] for _ in range(3)]

    assert results[:2] == [codes.CodeResult.WRONG, codes.CodeResult.WRONG]
    assert results[2] == codes.CodeResult.EXHAUSTED
    # And the row is gone - even the right code is useless now.
    assert await migrated.scalar(select(func.count()).select_from(AuthCode)) == 0


async def test_a_new_code_replaces_the_old_one(migrated: AsyncSession, user: User) -> None:
    """ "The old one will not work even if you find it."""
    old = await codes.issue(migrated, user, "reset")
    await codes.issue(migrated, user, "reset")

    result, _ = await codes.verify(migrated, user, "reset", old)

    assert result != codes.CodeResult.OK


async def test_codes_have_six_digits_and_keep_leading_zeros() -> None:
    seen = {codes.generate_code() for _ in range(200)}

    assert all(len(code) == 6 and code.isdigit() for code in seen)
    # Two hundred draws are overwhelmingly likely to differ.
    assert len(seen) > 1


# --- forgot: honest about delivery -------------------------------------------


async def test_forgot_says_unavailable_when_there_is_no_mail_server(
    signed_up: AsyncClient,
) -> None:
    """The `no_mail` state is a designed answer, not an error."""
    response = await signed_up.post("/api/auth/forgot", json={"username": USERNAME})

    assert response.status_code == 200
    assert response.json()["delivery"] == "unavailable"


async def test_forgot_answers_identically_for_unknown_accounts(
    signed_up: AsyncClient,
) -> None:
    """ "The screen says the same thing whether or not the account exists."""
    real = await signed_up.post("/api/auth/forgot", json={"username": USERNAME})
    fake = await signed_up.post("/api/auth/forgot", json={"username": "nobody"})

    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()


# --- The SSH key -------------------------------------------------------------


@pytest.fixture
def keypair(tmp_path: Path) -> tuple[Path, str]:
    """A real Ed25519 keypair, generated by the real ssh-keygen."""
    private = tmp_path / "id_ed25519"
    subprocess.run(  # noqa: S603
        [ssh_keys.ssh_keygen_path(), "-t", "ed25519", "-N", "", "-q", "-f", str(private)],
        check=True,
    )
    return private, (tmp_path / "id_ed25519.pub").read_text(encoding="utf-8").strip()


def _sign(private_key: Path, message: str) -> str:
    """Sign exactly as the screen tells the administrator to."""
    result = subprocess.run(  # noqa: S603
        [
            ssh_keys.ssh_keygen_path(),
            "-Y",
            "sign",
            "-f",
            str(private_key),
            "-n",
            ssh_keys.NAMESPACE,
        ],
        input=message.encode("utf-8"),
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8")


@needs_ssh_keygen
def test_a_real_signature_verifies_and_a_tampered_one_does_not(
    keypair: tuple[Path, str],
) -> None:
    private, public = keypair
    message = "ta1-verify-me"
    signature = _sign(private, message)

    assert ssh_keys.verify_signature(message=message, signature=signature, public_key=public)
    assert not ssh_keys.verify_signature(
        message="ta1-something-else", signature=signature, public_key=public
    )


@needs_ssh_keygen
def test_a_signature_from_another_namespace_is_refused(keypair: tuple[Path, str]) -> None:
    """The namespace scopes signatures to this application; `file` is what
    `ssh-keygen` uses by default, so a general-purpose signature must not sign in."""
    private, public = keypair
    message = "ta1-cross-namespace"
    foreign = subprocess.run(  # noqa: S603
        [ssh_keys.ssh_keygen_path(), "-Y", "sign", "-f", str(private), "-n", "file"],
        input=message.encode("utf-8"),
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")

    assert not ssh_keys.verify_signature(message=message, signature=foreign, public_key=public)


@needs_ssh_keygen
async def test_the_whole_key_flow_signs_in(
    signed_up: AsyncClient, migrated: AsyncSession, keypair: tuple[Path, str]
) -> None:
    """Challenge -> sign locally -> verify -> session. The flow the screen draws."""
    private, public = keypair
    user = await migrated.scalar(select(User))
    migrated.add(UserKey(user_id=user.id, public_key=public, label="test laptop"))
    await migrated.commit()

    challenge = (
        await signed_up.post("/api/auth/key/challenge", json={"username": USERNAME})
    ).json()
    assert challenge["challenge"].startswith("ta1-")
    assert challenge["namespace"] == ssh_keys.NAMESPACE

    response = await signed_up.post(
        "/api/auth/key/verify",
        json={
            "username": USERNAME,
            "challenge": challenge["challenge"],
            "signature": _sign(private, challenge["challenge"]),
        },
    )

    assert response.status_code == 200
    assert response.cookies[COOKIE_NAME]
    # And the session reaches a protected route.
    assert (await signed_up.get("/api/auth/me")).status_code == 200


@needs_ssh_keygen
async def test_a_challenge_is_single_use(
    signed_up: AsyncClient, migrated: AsyncSession, keypair: tuple[Path, str]
) -> None:
    """A captured signature must be worthless the second time."""
    private, public = keypair
    user = await migrated.scalar(select(User))
    migrated.add(UserKey(user_id=user.id, public_key=public))
    await migrated.commit()

    challenge = (
        await signed_up.post("/api/auth/key/challenge", json={"username": USERNAME})
    ).json()["challenge"]
    signature = _sign(private, challenge)
    payload = {"username": USERNAME, "challenge": challenge, "signature": signature}

    first = await signed_up.post("/api/auth/key/verify", json=payload)
    replay = await signed_up.post("/api/auth/key/verify", json=payload)

    assert first.status_code == 200
    assert replay.status_code == 401


async def test_an_unregistered_key_cannot_sign_in(
    signed_up: AsyncClient,
) -> None:
    challenge = (
        await signed_up.post("/api/auth/key/challenge", json={"username": USERNAME})
    ).json()["challenge"]

    response = await signed_up.post(
        "/api/auth/key/verify",
        json={"username": USERNAME, "challenge": challenge, "signature": "garbage"},
    )

    assert response.status_code == 401


async def test_a_challenge_is_minted_for_unknown_usernames_too(
    signed_up: AsyncClient,
) -> None:
    """Refusing would turn the endpoint into a directory of accounts."""
    response = await signed_up.post("/api/auth/key/challenge", json={"username": "nobody"})

    assert response.status_code == 200
    assert response.json()["challenge"].startswith("ta1-")


# --- The new password --------------------------------------------------------


async def test_the_last_five_passwords_cannot_come_back(
    migrated: AsyncSession, user: User
) -> None:
    """ "The server keeps the fingerprints of recent passwords."""
    await set_password(migrated, user, NEW_PASSWORD)

    with pytest.raises(PasswordReused):
        await set_password(migrated, user, NEW_PASSWORD)


async def test_an_old_password_beyond_the_window_is_allowed_again(
    migrated: AsyncSession, user: User
) -> None:
    """Five fingerprints, as the screen says.

    "Your last five passwords" includes the one in use: the current one plus four
    before it. Five further changes push NEW_PASSWORD out of that window.
    """
    await set_password(migrated, user, NEW_PASSWORD)
    for index in range(HISTORY_DEPTH):
        await set_password(migrated, user, f"an intermediate password number {index}")

    # NEW_PASSWORD has rolled out of the window of five.
    await set_password(migrated, user, NEW_PASSWORD)
    assert verify_password(NEW_PASSWORD, user.password_hash)


async def test_changing_the_password_ends_every_session(
    signed_up: AsyncClient, migrated: AsyncSession
) -> None:
    """ "Every other browser and phone signed in to this account is signed out."""
    await signed_up.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert await migrated.scalar(select(func.count()).select_from(Session)) == 1
    user = await migrated.scalar(select(User))

    await set_password(migrated, user, NEW_PASSWORD)

    assert await migrated.scalar(select(func.count()).select_from(Session)) == 0
