"""Text-to-speech providers. ``cancel()`` is mandatory, not optional."""

from __future__ import annotations

from agent.config import TtsSettings, tts_settings
from agent.providers.tts.base import TTSProvider
from agent.providers.tts.elevenlabs import ElevenLabsTTS

__all__ = [
    "ElevenLabsTTS",
    "TTSProvider",
    "configured_tts",
    "tts_for",
]


def tts_for(settings: TtsSettings) -> TTSProvider:
    """The implementation for a configuration. One service today, a dict the day of two."""
    return ElevenLabsTTS(settings)


def configured_tts() -> TTSProvider | None:
    """What the environment says should speak, or `None` when nothing is configured."""
    settings = tts_settings()
    return None if settings is None else tts_for(settings)
