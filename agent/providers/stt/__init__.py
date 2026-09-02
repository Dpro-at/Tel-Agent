"""Speech-to-text providers: stream(audio) -> partial and final transcripts."""

from __future__ import annotations

from agent.config import SttSettings, stt_settings
from agent.providers.stt.base import Final, Partial, STTProvider, Transcript
from agent.providers.stt.deepgram import DeepgramSTT

__all__ = [
    "DeepgramSTT",
    "Final",
    "Partial",
    "STTProvider",
    "Transcript",
    "configured_stt",
    "stt_for",
]


def stt_for(settings: SttSettings) -> STTProvider:
    """The implementation for a configuration. One service today, a dict the day of two."""
    return DeepgramSTT(settings)


def configured_stt() -> STTProvider | None:
    """What the environment says should listen, or `None` when nothing is configured."""
    settings = stt_settings()
    return None if settings is None else stt_for(settings)
