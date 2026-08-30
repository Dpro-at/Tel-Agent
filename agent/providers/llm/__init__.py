"""LLM providers: stream(messages, tools) -> token stream + tool calls."""

from __future__ import annotations

from agent.config import LlmSettings, llm_settings
from agent.providers.llm.base import LLMProvider, Message
from agent.providers.llm.openai_compatible import OpenAICompatibleLLM

__all__ = [
    "LLMProvider",
    "Message",
    "OpenAICompatibleLLM",
    "configured_provider",
    "provider_for",
]


def provider_for(settings: LlmSettings) -> LLMProvider:
    """The implementation named by a configuration.

    One `if` today and a dictionary the day there are three. It stays a function so the
    choice lives in one place: a second implementation added at the call sites is a
    second implementation that some call site does not know about.
    """
    if settings.provider == "openai":
        return OpenAICompatibleLLM(settings)
    # `agent.config` refuses an unsupported name before this is reached, so arriving
    # here means the two lists have drifted apart - which is a bug in this file.
    raise ValueError(f"no implementation for LLM provider {settings.provider!r}")


def configured_provider() -> LLMProvider | None:
    """What the environment says should answer, or `None` when nothing is configured."""
    settings = llm_settings()
    return None if settings is None else provider_for(settings)
