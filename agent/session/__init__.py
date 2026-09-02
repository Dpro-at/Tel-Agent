"""Conversation lifecycle, turn-taking, barge-in and whisper handling."""

from __future__ import annotations

from agent.session.turn import AudioSink, TurnResult, speak

__all__ = ["AudioSink", "TurnResult", "speak"]
