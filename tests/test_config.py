"""Configuration is read from the environment, validated at startup, and fails loudly.

B2's acceptance condition is that a missing or wrong required variable produces a clear
startup error naming it. A validator nobody tests is a validator that quietly stops
matching the message it promises.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.config import Settings, get_settings


def _settings(**overrides: object) -> Settings:
    """Build settings ignoring any `.env` on the contributor's machine."""
    return Settings(_env_file=None, **overrides)


def test_defaults_let_a_fresh_clone_start() -> None:
    settings = _settings()

    assert settings.environment == "development"
    assert settings.debug is True
    # D-029: the file-based dialect is the default, so nothing has to be installed.
    assert settings.database_url.startswith("sqlite+aiosqlite:")


def test_production_requires_an_encryption_key() -> None:
    with pytest.raises(ValidationError) as error:
        _settings(environment="production")

    message = str(error.value)
    assert "ENCRYPTION_KEY" in message
    assert "openssl rand -hex 32" in message


def test_production_starts_once_the_key_is_present() -> None:
    settings = _settings(environment="production", encryption_key="0" * 64)

    assert settings.debug is False


def test_an_empty_encryption_key_reads_as_absent_everywhere() -> None:
    """`ENCRYPTION_KEY=""` in a `.env` must mean the same thing in every reader.

    It used to read as *present* to `settings.encryption_key is not None` and as
    *absent* to `crypto.key_available()` — two answers to one question. Normalised at
    the settings boundary, so every reader downstream agrees. Whitespace is the same
    case wearing a space.
    """
    for raw in ("", "   "):
        assert _settings(encryption_key=raw).encryption_key is None


def test_an_empty_key_still_refuses_production() -> None:
    with pytest.raises(ValidationError) as error:
        _settings(environment="production", encryption_key="")

    assert "ENCRYPTION_KEY" in str(error.value)


def test_a_synchronous_database_driver_is_refused() -> None:
    """A blocking driver stalls the event loop, and only under load."""
    with pytest.raises(ValidationError) as error:
        _settings(database_url="postgresql://user:pass@localhost/telagent")

    assert "async driver" in str(error.value)


@pytest.mark.parametrize(
    "url",
    ["sqlite+aiosqlite:///./tel-agent.db", "postgresql+asyncpg://user:pass@localhost/telagent"],
)
def test_both_supported_dialects_are_accepted(url: str) -> None:
    """D-029: SQLite and PostgreSQL, both from the first migration."""
    assert _settings(database_url=url).database_url == url


def test_a_wildcard_cors_origin_is_refused() -> None:
    """The dashboard sends credentials; `*` with credentials is the shortcut that ships."""
    with pytest.raises(ValidationError) as error:
        _settings(cors_origins=["*"])

    assert "must not contain '*'" in str(error.value)


def test_comma_separated_origins_are_parsed() -> None:
    """A `.env` file holds strings, not JSON lists."""
    settings = _settings(cors_origins="http://localhost:38471, https://telagent.local")

    assert settings.cors_origins == ["http://localhost:38471", "https://telagent.local"]


def test_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("CORS_ORIGINS", "https://telagent.local")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.log_level == "WARNING"
    assert settings.cors_origins == ["https://telagent.local"]


def test_an_invalid_log_level_names_the_variable() -> None:
    with pytest.raises(ValidationError) as error:
        _settings(log_level="CHATTY")

    assert "log_level" in str(error.value).lower()
