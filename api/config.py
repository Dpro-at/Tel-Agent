"""Installation configuration, read from the environment and nowhere else.

`os.getenv` does not appear anywhere outside this module. That is not style: §B9.2 of
the specification splits secrets into two homes — installation secrets in `.env`, and
user-entered credentials encrypted in the database — and configuration read from a
second place is how that split starts to leak.

Everything here is validated at import of `settings`. A missing or malformed required
variable stops the process with a message naming the variable, rather than surfacing
three screens later as a confusing failure.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _installed_version() -> str:
    """The version reported by `/health`, taken from package metadata.

    Reading it from the installed distribution rather than a constant means the number
    cannot drift from `pyproject.toml`. An editable install that has not been built
    reports the fallback, which is honest: nothing knows the version at that point.
    """
    try:
        return package_version("tel-agent")
    except PackageNotFoundError:  # pragma: no cover - only on a non-installed tree
        return "0.0.0+unknown"


class Settings(BaseSettings):
    """Every installation variable, in one place.

    Defaults are chosen so a fresh clone starts without a `.env` at all. The one
    exception is `ENCRYPTION_KEY`, which is required in production and refuses to be
    guessed — see `_require_encryption_key_in_production`.
    """

    # Anchored to the repository, not the working directory - the same trap the
    # SQLite path fell into: `uvicorn` started from one directory and a script run
    # from another would otherwise read two different `.env` files, and the symptom
    # is settings that silently do not apply rather than an error naming the cause.
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[1] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # D-029: SQLAlchemy against both dialects. The default is the file-based option the
    # install wizard offers first — it needs nothing installed, which is what makes a
    # fresh clone runnable.
    database_url: str = "sqlite+aiosqlite:///./tel-agent.db"

    # B6. The dashboard is served separately in development, so the development origin
    # is allowed by default and nothing else is. A wildcard is rejected outright below:
    # credentials are sent with dashboard requests, and `*` with credentials is the
    # misconfiguration people reach for when CORS is in their way.
    #
    # `NoDecode` is load-bearing. Without it pydantic-settings tries to JSON-decode a
    # list-typed field straight from the environment, which fails before any validator
    # runs - and `CORS_ORIGINS=https://telagent.local` is exactly what a `.env` holds.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    trusted_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )

    # Pooling applies to PostgreSQL only. SQLite has no server to pool connections
    # to, and `api/db.py` does not pass these through for it.
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout: int = Field(default=30, ge=1, le=300)
    # Echoes every statement. Useful once, noisy always - off unless asked for.
    database_echo: bool = False

    # G2. Ceilings on one request, against a mistake rather than an attacker: an
    # unbounded body and an unbounded handler are each a denial of service that nobody
    # has to intend. A megabyte is far above every body this API accepts - nothing here
    # takes an upload - and thirty seconds is far above every handler, so both are a
    # backstop rather than a budget anything runs near.
    #
    # The timeout is measured to the first byte of the response, never to the last:
    # `api/middleware/limits.py` cancels it when the response starts, because a wall
    # clock over a whole response would cut off exactly the streamed replies Rule 3
    # exists to make possible.
    max_body_bytes: int = Field(default=1_048_576, ge=1024, le=104_857_600)
    request_timeout_seconds: int = Field(default=30, ge=1, le=300)

    # G1. Off unless an operator sets it, and the default is not timidity. Most
    # installations are a machine on a business network reached over plain HTTP, and a
    # Strict-Transport-Security header sent to one of those makes the dashboard
    # unreachable from every browser that saw it, for as long as the header said, with
    # no way to undo it from the server. Seconds; 31536000 is a year, which is what an
    # installation with TLS everywhere should eventually use.
    hsts_seconds: int = Field(default=0, ge=0, le=63072000)
    # G3. Where the server listens, and it is loopback because §B9's three supported
    # paths - a private network, a VPN, a reverse proxy terminating TLS - all reach a
    # process on 127.0.0.1, and none of them needs one on every interface. Applied by
    # `python -m api`, which is why that is the documented way to start it: a default
    # nothing applies is decoration.
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8000, ge=1, le=65535)

    # Mail. Most installations have none, and that is a designed state rather than a
    # broken one: the `forgot` screen says so and points at a command on the machine.
    # `smtp_host` being unset is what puts the API into that answer.
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False

    # The in-process scheduler. Off in tests, which drive the runner directly rather
    # than racing a loop; off in a second API process once one exists, so that exactly
    # one clock ticks.
    jobs_enabled: bool = True

    # §B9.2. This key encrypts every user-entered credential stored in the database.
    # It must never sit in the same place as the data it protects.
    encryption_key: str | None = None

    # Where the Telegram Bot API lives. Nobody changes this in production; tests and
    # development point it at a stand-in so the transport can be exercised without a
    # bot, a network, or Telegram's cooperation.
    telegram_api_base: str = "https://api.telegram.org"

    # Where Meta's Graph API lives, version included. Same reasoning: a stand-in for
    # development and tests, never changed in production.
    whatsapp_api_base: str = "https://graph.facebook.com/v23.0"
    # Messenger and Instagram speak to the same Graph API; a separate setting so a
    # test can stand in for one product without impersonating the other.
    meta_api_base: str = "https://graph.facebook.com/v23.0"

    @property
    def version(self) -> str:
        return _installed_version()

    @property
    def debug(self) -> bool:
        return self.environment == "development"

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept `A,B` as well as a JSON list, because a `.env` file holds strings."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _refuse_wildcard_origin(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError(
                "CORS_ORIGINS must not contain '*'. The dashboard sends credentials, "
                "and a wildcard origin with credentials is refused by browsers anyway. "
                "List the origins explicitly."
            )
        return value

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """A synchronous driver here stalls the event loop under load.

        The failure it causes is the one that only appears in production, so it is
        caught at startup instead.
        """
        if not value.startswith(("sqlite+aiosqlite:", "postgresql+asyncpg:")):
            raise ValueError(
                "DATABASE_URL must use an async driver: 'sqlite+aiosqlite://' or "
                f"'postgresql+asyncpg://'. Got: {value.split('://')[0]}://"
            )
        return value

    @model_validator(mode="after")
    def _require_encryption_key_in_production(self) -> Settings:
        if self.environment == "production" and not self.encryption_key:
            raise ValueError(
                "ENCRYPTION_KEY is required when ENVIRONMENT=production. It encrypts "
                "every stored credential. Generate one with: openssl rand -hex 32"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """The settings singleton.

    Cached so that the environment is read once per process. Tests clear the cache
    rather than reaching for a second instance — see `tests/conftest.py`.
    """
    return Settings()
