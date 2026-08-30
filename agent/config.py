"""What the agent reads from the environment.

`agent/` never imports from `api/` (docs/ARCHITECTURE.md rule 3), so it does not share
`api.config.Settings`. That separation is not an inconvenience to work around: at
Milestone 11 the agent is a process joining a room, started by whoever runs the media
path, and it has to be configurable without an API server existing at all.

Environment variables only, per the code conventions - and read once, because a value
that can change under a running call is a value that will change under a running call.
"""

from __future__ import annotations

import os
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


@lru_cache(maxsize=1)
def llm_settings() -> LlmSettings | None:
    """The configured model, or `None` when no model is configured.

    `None` is a supported state, not a failure: an installation that has not connected
    a model still takes messages, and `agent.reply` says so in words rather than
    failing a request the visitor cannot do anything about.

    A *half*-configured model is a different thing and raises. A provider named with no
    key behind it is somebody who meant to connect a model and has not finished; a
    silent fallback there would look exactly like the model answering badly.
    """
    provider = _clean("LLM_PROVIDER")
    if not provider:
        return None

    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError(
            f"LLM_PROVIDER={provider!r} is not one this build implements. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )

    model, api_key = _clean("LLM_MODEL"), _clean("LLM_API_KEY")
    missing = [
        name for name, value in (("LLM_MODEL", model), ("LLM_API_KEY", api_key)) if not value
    ]
    if missing:
        raise ConfigurationError(
            f"LLM_PROVIDER is set to {provider!r} but {' and '.join(missing)} "
            "is empty. Set it, or clear LLM_PROVIDER to run without a model."
        )

    return LlmSettings(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=_clean("LLM_BASE_URL").rstrip("/") or DEFAULT_BASE_URL,
    )
