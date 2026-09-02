"""Speaking a turn, and stopping the instant the caller cuts in — Rule 3.

This is the half of Milestone 11 that every other milestone was written to make
possible. `agent.reply.reply` already yields text a piece at a time rather than a
finished paragraph; here that text is spoken *while the rest is still being generated*,
and the speaking stops mid-word when the caller talks over it.

**Barge-in is two acts, and both are here.** Stopping the synthesiser (closing the TTS
iterator, whose `finally` closes the provider's response and stops the far-end billing)
and flushing the audio already handed to the sink (the queued speech ahead of the
listener). Doing only the first leaves the agent finishing its sentence over the caller
from the playback buffer; doing only the second keeps paying for audio nobody hears.

**Text is spoken in phrases, not per token nor all at once.** A token is too small to
synthesise well and all-at-once reintroduces the silence Rule 3 forbids. So the reply's
tokens are gathered to the next sentence boundary and that phrase is sent to the TTS,
which begins the next phrase while the current one is still playing — the first sentence
speaks while the rest generates.

**Latency is measured from the end of the caller's speech**, which the caller passes in
as `since` (the moment endpointing fired), to the first byte of audio out. That is the
number Rule 3 budgets at 800 ms and Rule 4 logs on every call; this module records it
and the caller decides what to do with a turn that missed.

`agent/` may not import `api/`, so nothing here stores a transcript or touches a
session: the turn produces text and audio and a measurement, and the caller that has a
database decides what those mean.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

from agent.providers.tts import TTSProvider

logger = logging.getLogger("agent.session")

# Where a phrase ends: sentence punctuation, or a comma once the phrase is long enough
# that waiting for a full stop would delay the first audio past the budget. Tuned for
# speech, not prose - a caller would rather hear the first clause a beat early than the
# whole sentence a beat late.
_SENTENCE_END = re.compile(r"[.!?]['\")\]]?\s")
_SOFT_BREAK = re.compile(r"[,;:]['\")\]]?\s")
# Below this a comma is not worth breaking on; above it, break at the first comma so a
# long sentence does not hold the first audio hostage to its full stop.
_SOFT_BREAK_MIN = 60


@dataclass
class TurnResult:
    """What one spoken turn produced, for the transcript and for Rule 4."""

    # The whole text the agent said, assembled from the phrases - one transcript line.
    text: str = ""
    # End of caller speech to first audio out, in milliseconds. None when the agent
    # said nothing (an empty reply) so nothing was ever spoken.
    first_audio_ms: float | None = None
    # True when the caller cut in and the turn was cut short. The stored line is what
    # was actually said before the interruption, not what the model would have finished.
    interrupted: bool = False
    # Every phrase's first-audio latency, so a turn that started fast and then stalled
    # is visible rather than averaged away.
    phrase_latencies_ms: list[float] = field(default_factory=list)


class AudioSink(Protocol):
    """Where synthesised audio goes, and the flush that barge-in needs.

    The transport implements this - LiveKit, or a SIP media stream - and it owns the
    playback buffer. `flush` discards whatever is buffered but not yet heard, which is
    the act closing the TTS iterator cannot perform because the provider never sees the
    buffer.
    """

    async def write(self, chunk: bytes) -> None:
        """Queue one audio chunk for playback."""
        ...

    async def flush(self) -> None:
        """Discard queued-but-unplayed audio. Called on barge-in."""
        ...


def _phrases(buffer: str, *, final: bool) -> tuple[list[str], str]:
    """Split what has arrived so far into complete phrases and a remainder.

    `final` true flushes the remainder as a last phrase - the reply has ended, so the
    tail is spoken rather than held for a boundary that will never come.
    """
    phrases: list[str] = []
    while True:
        match = _SENTENCE_END.search(buffer)
        if match is None and len(buffer) >= _SOFT_BREAK_MIN:
            match = _SOFT_BREAK.search(buffer)
        if match is None:
            break
        cut = match.end()
        phrase = buffer[:cut].strip()
        if phrase:
            phrases.append(phrase)
        buffer = buffer[cut:]
    if final:
        tail = buffer.strip()
        if tail:
            phrases.append(tail)
        buffer = ""
    return phrases, buffer


async def speak(
    text_stream: AsyncIterator[str],
    *,
    tts: TTSProvider,
    sink: AudioSink,
    should_stop: Callable[[], bool],
    since: float | None = None,
) -> TurnResult:
    """Speak a reply as it streams, stopping the instant `should_stop()` is true.

    `text_stream` is `agent.reply.reply(...)`. `should_stop` is polled between audio
    chunks and is how the caller wires barge-in - it returns true the moment the audio
    input side hears the caller start again. `since` is the end-of-speech instant for
    the latency measurement; omitted, latency is measured from the first phrase.
    """
    result = TurnResult()
    started = since if since is not None else time.perf_counter()
    buffer = ""
    said: list[str] = []

    async def emit(phrase: str) -> bool:
        """Speak one phrase. Returns False if barge-in cut it short."""
        phrase_start = time.perf_counter()
        first_chunk = True
        audio = tts.stream(phrase)
        try:
            async for chunk in audio:
                if should_stop():
                    return False
                await sink.write(chunk)
                if first_chunk:
                    first_chunk = False
                    latency = (time.perf_counter() - phrase_start) * 1000
                    result.phrase_latencies_ms.append(latency)
                    if result.first_audio_ms is None:
                        result.first_audio_ms = (time.perf_counter() - started) * 1000
        finally:
            # Closes the provider's response - stops synthesis (and its bill) at the far
            # end rather than only ignoring what it sends. Runs whether the phrase
            # finished or barge-in returned early.
            await audio.aclose()
        return True

    try:
        async for piece in text_stream:
            if should_stop():
                result.interrupted = True
                break
            buffer += piece
            phrases, buffer = _phrases(buffer, final=False)
            for phrase in phrases:
                said.append(phrase)
                if not await emit(phrase):
                    result.interrupted = True
                    break
            if result.interrupted:
                break
        else:
            # The reply ended on its own; speak whatever tail is left.
            phrases, buffer = _phrases(buffer, final=True)
            for phrase in phrases:
                if should_stop():
                    result.interrupted = True
                    break
                said.append(phrase)
                if not await emit(phrase):
                    result.interrupted = True
                    break
    finally:
        # Whatever ended this - a clean finish or barge-in - the reply generator is
        # closed so its own `finally` stops the model mid-generation and stops that bill
        # too. Closing an exhausted generator is harmless.
        await text_stream.aclose()

    if result.interrupted:
        # The queued-but-unheard audio is discarded here: closing the TTS iterator
        # stopped production, this stops playback of what was already handed over.
        await sink.flush()

    result.text = " ".join(said).strip()
    if result.first_audio_ms is not None:
        logger.info(
            "spoken turn",
            extra={
                "first_audio_ms": round(result.first_audio_ms),
                "interrupted": result.interrupted,
                "phrases": len(result.phrase_latencies_ms),
            },
        )
    return result
