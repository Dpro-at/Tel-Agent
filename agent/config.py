"""What a configured model is, and how the environment describes one.

`agent/` never imports from `api/` (docs/ARCHITECTURE.md rule 3), so it does not share
`api.config.Settings`. That separation is not an inconvenience to work around: at
Milestone 11 the agent is a process joining a room, started by whoever runs the media
path, and it has to be configurable without an API server existing at all.

**Since §B9.2 the environment is no longer the only source.** The key a user types into
the settings screen lives encrypted in the database, and the database is on the far
side of the boundary this package may not cross. So the rule that decides whether a
configuration is *whole* lives here, in `settings_from`, and whoever can read a source
calls it: `llm_settings()` for the environment, `api.llm.resolve` for the store. The
agent never learns which one answered, which is exactly what keeps it free of `api/`.

The environment path is read once, because a value that can change under a running call
is a value that will change under a running call. The store path is read per turn -
§B9.2 says a credential entered from the screen takes effect immediately - and a turn
holds the settings it started with.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

# The default for an OpenAI-compatible endpoint. Named `openai` because that is the
# shape of the API - `POST /chat/completions` with server-sent events - and not the
# name of one company's service: the same code reaches a local model, a hosted
# gateway, or anything else that speaks it, by pointing `LLM_BASE_URL` elsewhere.
DEFAULT_BASE_URL = "https://api.openai.com/v1"

SUPPORTED_PROVIDERS = ("openai",)


class ConfigurationError(RuntimeError):
    """Half a configuration. Louder than a default, because a default would be wrong."""


@dataclass(frozen=True)
class LlmSettings:
    """Which model answers, and where it lives."""

    provider: str
    model: str
    api_key: str
    base_url: str


def _clean(name: str) -> str:
    return os.environ.get(name, "").strip()


def _sentence(text: str) -> str:
    """Capitalise the first letter only.

    The message opens with whatever the source calls the field, and one of those is the
    screen's `the provider` - a refusal that begins in lower case reads as a string
    somebody forgot to finish. `str.capitalize` is wrong here: it would lower-case
    `LLM_PROVIDER` in the other source's version of the same sentence.
    """
    return text[:1].upper() + text[1:]


# What each of the four values is called, when the environment is the source. The
# message a half-configured installation gets has to name the thing the person can
# actually go and fix, and that name differs per source - `LLM_API_KEY` means nothing
# to somebody who typed the key into a form.
ENVIRONMENT_NAMES: Mapping[str, str] = {
    "provider": "LLM_PROVIDER",
    "model": "LLM_MODEL",
    "api_key": "LLM_API_KEY",
}


def settings_from(
    *,
    provider: str,
    model: str,
    api_key: str,
    base_url: str,
    names: Mapping[str, str],
) -> LlmSettings | None:
    """The configured model these four values describe, or `None` for no model.

    `None` is a supported state, not a failure: an installation that has not connected
    a model still takes messages, and `agent.reply` says so in words rather than
    failing a request the visitor cannot do anything about.

    A *half*-configured model is a different thing and raises. A provider named with no
    key behind it is somebody who meant to connect a model and has not finished; a
    silent fallback there would look exactly like the model answering badly.

    `names` is what to call each value in that refusal - see `ENVIRONMENT_NAMES`.
    """
    provider, model, api_key = provider.strip(), model.strip(), api_key.strip()
    if not provider:
        return None

    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError(
            f"{provider!r} is not a provider this build implements. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    missing = [
        names[field] for field, value in (("model", model), ("api_key", api_key)) if not value
    ]
    if missing:
        raise ConfigurationError(
            _sentence(
                f"{names['provider']} is set to {provider!r} but {' and '.join(missing)} "
                f"is empty. Set it, or clear {names['provider']} to run without a model."
            )
        )

    return LlmSettings(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url.strip().rstrip("/") or DEFAULT_BASE_URL,
    )


def environment_values() -> dict[str, str]:
    """The four values as the environment gives them, unvalidated.

    Separate from `llm_settings` because a dashboard merges these with what the owner
    saved, and a merge cannot start from a function that refuses half a configuration:
    an installer's `LLM_PROVIDER` with the key now living in the store is *not* half a
    configuration, it is the migration §B9.2 describes. Validation happens once, over
    the merged four, in `settings_from`.
    """
    return {
        "provider": _clean("LLM_PROVIDER"),
        "model": _clean("LLM_MODEL"),
        "api_key": _clean("LLM_API_KEY"),
        "base_url": _clean("LLM_BASE_URL"),
    }


@lru_cache(maxsize=1)
def llm_settings() -> LlmSettings | None:
    """The model the *environment* describes, or `None` when it names none.

    This is the installer's path and the standalone one: at Milestone 11 the agent runs
    as its own process with no API server and no settings screen behind it, and this is
    how it is configured there. Where a dashboard exists, `api.llm.resolve` prefers what
    the owner saved over what an installer wrote once - see §B9.2.
    """
    return settings_from(**environment_values(), names=ENVIRONMENT_NAMES)
