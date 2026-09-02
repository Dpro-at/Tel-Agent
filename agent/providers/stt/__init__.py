"""Speech-to-text providers: stream(audio) -> partial and final transcripts."""

from __future__ import annotations

from agent.providers.stt.base import Final, Partial, STTProvider, Transcript

__all__ = ["Final", "Partial", "STTProvider", "Transcript"]
