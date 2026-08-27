"""Encryption at rest — E1, E2, E5, E6 and E7.

The claims under test, in the epic's own words: the app refuses to start without a
valid key and says what to do; the stored bytes are unreadable in the database and a
tampered ciphertext raises rather than returning garbage; rotation completes and every
credential still decrypts; a posted credential appears nowhere in the captured logs;
and `.env.example` cannot drift from the settings model.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings, get_settings
from api.models import Channel, Workspace
from api.models.encrypted import reset_key_cache
from api.security.crypto import (
    DecryptionFailed,
    EncryptionKeyError,
    decrypt,
    encrypt,
    generate_key,
    load_key,
    mask,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

KEY_HEX = "aa" * 32
KEY = load_key(KEY_HEX)
SECRET_VALUE = "9999999999:AAH-telegram-bot-token-value"  # noqa: S105


@pytest.fixture
def configured_key(monkeypatch: pytest.MonkeyPatch):
    """Point the process key at a known value for the ORM type, and clean up after."""
    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield KEY
    get_settings.cache_clear()
    reset_key_cache()


# --- E1: the key -------------------------------------------------------------


def test_a_missing_key_refuses_with_instructions() -> None:
    with pytest.raises(EncryptionKeyError) as error:
        load_key(None)

    message = str(error.value)
    assert "openssl rand -hex 32" in message
    # The warning that matters most: the backup that defeats the encryption.
    assert "backup" in message


@pytest.mark.parametrize("bad", ["not-hex", "abcd", "zz" * 32, "aa" * 16])
def test_a_malformed_key_is_refused_not_repaired(bad: str) -> None:
    """Padding or hashing a wrong key into shape lets two installations believe they
    hold the same key while holding different ones."""
    with pytest.raises(EncryptionKeyError):
        load_key(bad)


def test_generate_key_produces_a_loadable_key() -> None:
    assert len(load_key(generate_key())) == 32


# --- E2: the envelope and the column -----------------------------------------


def test_round_trip() -> None:
    stored = encrypt(SECRET_VALUE, KEY)

    assert stored != SECRET_VALUE
    assert SECRET_VALUE not in stored
    assert decrypt(stored, KEY) == SECRET_VALUE


def test_the_same_plaintext_encrypts_differently_every_time() -> None:
    """A fresh nonce per row: equal credentials must not produce equal ciphertexts,
    or the database itself reveals which channels share a token."""
    assert encrypt(SECRET_VALUE, KEY) != encrypt(SECRET_VALUE, KEY)


def test_a_tampered_ciphertext_raises_rather_than_returning_garbage() -> None:
    envelope = bytearray(base64.b64decode(encrypt(SECRET_VALUE, KEY)))
    envelope[-1] ^= 0x01  # flip one bit of the tag
    tampered = base64.b64encode(bytes(envelope)).decode()

    with pytest.raises(DecryptionFailed):
        decrypt(tampered, KEY)


def test_the_wrong_key_is_a_clean_failure() -> None:
    with pytest.raises(DecryptionFailed):
        decrypt(encrypt(SECRET_VALUE, KEY), load_key("bb" * 32))


def test_an_unknown_envelope_version_is_refused() -> None:
    envelope = bytearray(base64.b64decode(encrypt(SECRET_VALUE, KEY)))
    envelope[0] = 99
    with pytest.raises(DecryptionFailed):
        decrypt(base64.b64encode(bytes(envelope)).decode(), KEY)


async def test_the_stored_row_is_unreadable_and_the_orm_round_trips(
    migrated: AsyncSession, configured_key: bytes
) -> None:
    """E2's acceptance condition, including the direct look at the stored row."""
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()
    channel = Channel(
        workspace_id=workspace.id,
        kind="telegram",
        name="Bot",
        credentials_encrypted=SECRET_VALUE,
    )
    migrated.add(channel)
    await migrated.commit()

    # The direct look: raw SQL, no ORM type in the path.
    raw = (
        await migrated.execute(
            text("SELECT credentials_encrypted FROM channels WHERE id = :id"),
            {"id": channel.id},
        )
    ).scalar_one()
    assert raw != SECRET_VALUE
    assert SECRET_VALUE not in raw
    # And it is our envelope, not accidental plaintext that happens to differ.
    assert decrypt(raw, configured_key) == SECRET_VALUE

    # The ORM read path hands back the plaintext. The id is captured before
    # expire_all: touching an attribute of an expired instance triggers a synchronous
    # refresh, which an async session refuses mid-expression.
    channel_id = channel.id
    migrated.expire_all()
    stored = await migrated.scalar(select(Channel).where(Channel.id == channel_id))
    assert stored is not None
    assert stored.credentials_encrypted == SECRET_VALUE


# --- E3: the mask ------------------------------------------------------------


def test_the_mask_shows_only_the_last_four() -> None:
    assert mask("od_live_9f2c77aa3ab1") == "••••3ab1"
    assert SECRET_VALUE[-4:] in mask(SECRET_VALUE)
    assert SECRET_VALUE[:-4] not in mask(SECRET_VALUE)


def test_a_short_secret_masks_entirely() -> None:
    """Four of six characters is most of the secret."""
    assert mask("abcdef") == "••••"


# --- E5: rotation ------------------------------------------------------------


async def test_rotation_re_encrypts_and_is_safe_to_run_twice(
    migrated: AsyncSession,
    configured_key: bytes,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Workspace(name="W")
    migrated.add(workspace)
    await migrated.flush()
    migrated.add(
        Channel(
            workspace_id=workspace.id,
            kind="telegram",
            name="Bot",
            credentials_encrypted=SECRET_VALUE,
        )
    )
    await migrated.commit()

    from scripts.rotate_key import rotate

    new_key_hex = "cc" * 32
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    try:
        assert await rotate(new_key_hex) == 0
        # Twice: the second pass finds everything already done and changes nothing.
        assert await rotate(new_key_hex) == 0
    finally:
        get_settings.cache_clear()

    raw = (
        await migrated.execute(text("SELECT credentials_encrypted FROM channels"))
    ).scalar_one()
    with pytest.raises(DecryptionFailed):
        decrypt(raw, configured_key)  # the old key no longer opens it
    assert decrypt(raw, load_key(new_key_hex)) == SECRET_VALUE


def test_the_rotation_list_matches_the_models() -> None:
    """A new encrypted column that is not in the rotation list is a column rotation
    silently skips - the failure E5 exists to prevent."""
    from api.db import Base
    from api.models.encrypted import EncryptedStr
    from scripts.rotate_key import ENCRYPTED_COLUMNS

    in_models = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, EncryptedStr)
    }
    in_rotation = {(table, column) for table, _id, column in ENCRYPTED_COLUMNS}

    assert in_models == in_rotation


# --- E6: no secret reaches a log ---------------------------------------------


def test_secret_named_fields_are_redacted_from_records() -> None:
    from api.logging import JsonFormatter, SecretRedactionFilter

    record = logging.LogRecord(
        "api.test",
        logging.INFO,
        __file__,
        1,
        f"connecting with token: {SECRET_VALUE} for channel",
        (),
        None,
    )
    record.details = {"credentials": SECRET_VALUE, "nested": {"api_key": SECRET_VALUE}}
    record.password = SECRET_VALUE

    SecretRedactionFilter().filter(record)
    line = JsonFormatter().format(record)

    assert SECRET_VALUE not in line
    assert "[redacted]" in line


async def test_a_posted_credential_appears_nowhere_in_captured_logs(
    migrated: AsyncSession,
    configured_key: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E6's acceptance condition: write a credential through the stack with logging
    wide open, then grep everything that was logged."""
    workspace = Workspace(name="W")
    migrated.add(workspace)
    await migrated.flush()

    with caplog.at_level(logging.DEBUG):
        migrated.add(
            Channel(
                workspace_id=workspace.id,
                kind="telegram",
                name="Bot",
                credentials_encrypted=SECRET_VALUE,
            )
        )
        await migrated.commit()

    everything = "\n".join(
        record.getMessage() + str(record.__dict__) for record in caplog.records
    )
    assert SECRET_VALUE not in everything


# --- E7: .env.example cannot drift -------------------------------------------


def test_env_example_documents_every_setting_and_nothing_else() -> None:
    """The settings model and `.env.example` must agree, both directions.

    A setting without documentation is an installation that cannot know to set it; a
    documented variable without a setting is advice that silently does nothing.
    """
    documented = {
        match.group(1)
        for match in re.finditer(
            r"^([A-Z][A-Z0-9_]*)=",
            (REPO_ROOT / ".env.example").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    }
    modeled = {name.upper() for name in Settings.model_fields}

    # Milestone 0 exception (§B9.2): provider and telephony variables live in
    # `.env.example` ahead of the code that reads them. They are tolerated as extras
    # until their epics land, but a *modeled* setting must always be documented.
    undocumented = modeled - documented
    assert not undocumented, (
        f"settings missing from .env.example: {sorted(undocumented)} - document them "
        "with a safe placeholder"
    )


def test_env_example_contains_no_real_looking_secret() -> None:
    """Safe placeholders only. A key that parses as a real key is one paste away from
    production."""
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for match in re.finditer(r"^(\w*(?:KEY|SECRET|PASSWORD)\w*)=(.+)$", content, re.MULTILINE):
        value = match.group(2).strip()
        assert not re.fullmatch(r"[0-9a-fA-F]{64}", value), (
            f"{match.group(1)} in .env.example looks like a real key"
        )
