"""Deepgram, behind the STT interface — §B3's v1 speech-to-text.

Deepgram's streaming API is a WebSocket: audio frames go up, JSON transcript messages
come down, and — crucially for Rule 3 — interim results come down *before* the caller
has stopped talking. Those interims are the `Partial`s the turn-taker uses to start
thinking during endpointing, the largest slice of the 800 ms budget; the `is_final`
messages are the `Final`s that become transcript lines.

**Two directions at once, joined here.** A sender task pumps the caller's audio up; the
generator reads messages down and yields transcripts. When the audio stream ends (the
caller hung up, or the turn's audio is done), it sends Deepgram's `CloseStream` so the
service flushes any last final rather than dropping a half-heard phrase, then drains the
remaining messages until the socket closes.

**The connection is a G.711 μ-law telephone call.** `encoding=mulaw&sample_rate=8000`
matches SIP_CODEC=PCMU, so the transport forwards the call's own frames with no
transcode on the media path (Rule 0). `interim_results=true` is what makes the partials
arrive; `language` is set rather than detected, because Rule 4's accuracy bar is a
German one and asking Deepgram to guess the language of a two-word answer is how it
guesses wrong.

**Cancellation is the generator's close.** Stopping consumption unwinds the `async with`
connection and cancels the sender; the socket closes, and Deepgram stops billing for a
stream nobody is reading. The key rides in the `Authorization` header and is never
logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import websockets

from agent.config import SttSettings
from agent.providers.stt.base import Final, Partial, Transcript

logger = logging.getLogger("agent.stt")

# The knobs that do not depend on the transport's codec. `interim_results` is what
# makes the partials arrive at all; punctuation is on because a transcript line without
# it reads worse and Deepgram's costs nothing. The encoding and sample rate come from
# the settings, because a SIP call is μ-law 8 kHz and a LiveKit room is 16-bit PCM.
_FIXED_PARAMS = {
    "channels": "1",
    "interim_results": "true",
    "punctuate": "true",
}


class STTError(RuntimeError):
    """The Deepgram socket failed in a way a retry would not fix."""


class DeepgramSTT:
    """Streams transcripts from a μ-law call. Satisfies `STTProvider` structurally."""

    def __init__(self, settings: SttSettings) -> None:
        self._settings = settings

    def _url(self) -> str:
        params = {
            **_FIXED_PARAMS,
            "encoding": self._settings.encoding,
            "sample_rate": str(self._settings.sample_rate),
            "model": self._settings.model,
            "language": self._settings.language,
        }
        return f"{self._settings.base_url}/v1/listen?{urlencode(params)}"

    def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        settings = self._settings
        url = self._url()

        async def gen() -> AsyncIterator[Transcript]:
            headers = {"Authorization": f"Token {settings.api_key}"}
            async with websockets.connect(url, additional_headers=headers) as ws:
                sender = asyncio.ensure_future(_pump(ws, audio))
                try:
                    async for raw in ws:
                        transcript = _read(raw, settings.language)
                        if transcript is not None:
                            yield transcript
                finally:
                    sender.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender

        return gen()


async def _pump(ws: websockets.ClientConnection, audio: AsyncIterator[bytes]) -> None:
    """Send the caller's audio up, then ask Deepgram to flush.

    `CloseStream` rather than just closing the socket: it tells Deepgram to finalise
    whatever it is mid-way through recognising, so the caller's last phrase becomes a
    `Final` instead of being lost with the connection.
    """
    try:
        async for chunk in audio:
            if chunk:
                await ws.send(chunk)
    except websockets.ConnectionClosed:
        return
    with contextlib.suppress(websockets.ConnectionClosed):
        await ws.send(json.dumps({"type": "CloseStream"}))


def _read(raw: str | bytes, fallback_language: str) -> Transcript | None:
    """One Deepgram message into a `Partial`, a `Final`, or nothing.

    Metadata, `UtteranceEnd` and empty transcripts are the "nothing": they are real
    messages, but not something a caller said, and a transcript line for them would be
    the archive inventing speech.
    """
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if message.get("type") != "Results":
        return None
    alternatives = (message.get("channel") or {}).get("alternatives") or []
    if not alternatives:
        return None
    text = str(alternatives[0].get("transcript") or "").strip()
    if not text:
        return None
    if message.get("is_final"):
        confidence = alternatives[0].get("confidence")
        language = (message.get("channel") or {}).get("detected_language") or fallback_language
        return Final(
            text=text,
            confidence=float(confidence) if isinstance(confidence, int | float) else None,
            language=language,
        )
    return Partial(text=text)
