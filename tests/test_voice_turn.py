"""Milestone 11 — speaking a turn, and stopping the instant the caller cuts in.

The interfaces (STT, TTS) are §B3's "abstraction written on day one"; this exercises
the turn-taker that speaks a streamed reply through them. The load-bearing test is the
barge-in one: Rule 3 makes `cancel()` mandatory, and "mandatory" means a test that
fails if queued audio is not thrown away. Everything here runs on fakes — no provider,
no line, no key, no cost — because the behaviour under test is the orchestration, and a
real provider would test the provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from agent.providers.stt.base import Final, Partial
from agent.providers.tts.base import TTSProvider
from agent.session import speak
from agent.session.turn import _phrases


class RecordingSink:
    """An `AudioSink` that keeps what it played and what survived a flush.

    `heard` is everything written; `flushed` counts barge-in flushes. A real sink
    would drop the queued frames; here we record that flush was asked for, because that
    request *is* the discard the transport then performs.
    """

    def __init__(self) -> None:
        self.heard: list[bytes] = []
        self.flushes = 0

    async def write(self, chunk: bytes) -> None:
        self.heard.append(chunk)

    async def flush(self) -> None:
        self.flushes += 1


class FakeTTS:
    """One audio chunk per whitespace-delimited word, so 'barge-in' is observable at
    word granularity. Records whether its generator was closed - the far-end stop."""

    def __init__(self) -> None:
        self.closed = False
        self.spoken: list[str] = []

    def stream(self, text: str) -> AsyncIterator[bytes]:
        self.spoken.append(text)

        async def gen() -> AsyncIterator[bytes]:
            try:
                for word in text.split():
                    yield word.encode()
            finally:
                self.closed = True

        return gen()


# A TTSProvider is a structural type; FakeTTS satisfies it.
_: TTSProvider = FakeTTS()


async def _stream(pieces: list[str]) -> AsyncIterator[str]:
    for piece in pieces:
        yield piece


# --- Phrase chunking --------------------------------------------------------------


def test_a_reply_is_cut_into_speakable_phrases() -> None:
    phrases, rest = _phrases("Yes, we are open. Until noon on ", final=False)
    # The full stop closes a phrase; the trailing clause waits for its boundary.
    assert phrases == ["Yes, we are open."]
    assert rest == "Until noon on "


def test_a_long_clause_breaks_on_a_comma_rather_than_holding_the_first_audio() -> None:
    # No full stop yet - the sentence is still forming - but long enough that waiting
    # for one would delay the first audio, so it breaks at the comma.
    long = "It depends on the day of the week and the season, but usually the "
    phrases, rest = _phrases(long, final=False)
    assert phrases[0].endswith(",")
    assert rest.startswith("but usually")


def test_the_tail_is_spoken_when_the_reply_ends_without_punctuation() -> None:
    phrases, rest = _phrases("no full stop here", final=True)
    assert phrases == ["no full stop here"]
    assert rest == ""


# --- The full turn ----------------------------------------------------------------


async def test_the_whole_reply_is_spoken_and_measured() -> None:
    tts = FakeTTS()
    sink = RecordingSink()
    result = await speak(
        _stream(["Yes, we are open. ", "Until noon."]),
        tts=tts,
        sink=sink,
        should_stop=lambda: False,
    )
    assert result.text == "Yes, we are open. Until noon."
    assert not result.interrupted
    assert result.first_audio_ms is not None
    # Every word became a chunk; nothing was flushed because nothing was interrupted.
    assert b"".join(sink.heard) == b"Yes,weareopen.Untilnoon."
    assert sink.flushes == 0
    assert tts.closed


async def test_an_empty_reply_says_nothing_and_measures_nothing() -> None:
    tts = FakeTTS()
    sink = RecordingSink()
    result = await speak(_stream([]), tts=tts, sink=sink, should_stop=lambda: False)
    assert result.text == ""
    assert result.first_audio_ms is None
    assert sink.heard == []


# --- Barge-in: the load-bearing case ----------------------------------------------


async def test_barge_in_stops_the_synthesiser_and_flushes_the_queue() -> None:
    """The caller talks over the agent. Production stops (the TTS generator is closed)
    and the queued audio is flushed - both, because either alone leaves the product
    broken: talking over the caller, or paying for unheard speech."""
    tts = FakeTTS()
    sink = RecordingSink()

    # The caller cuts in the moment the agent has said its first two words.
    def should_stop() -> bool:
        return len(sink.heard) >= 2

    result = await speak(
        _stream(["One two three four five. ", "And a whole second sentence. "]),
        tts=tts,
        sink=sink,
        should_stop=should_stop,
    )

    assert result.interrupted is True
    # It stopped at the barge-in, not at the end of the reply.
    assert len(sink.heard) == 2
    # The queue was flushed exactly once, and the synthesiser was closed (far-end stop).
    assert sink.flushes == 1
    assert tts.closed
    # The second sentence was never even sent to the TTS - the turn ended first.
    assert "And a whole second sentence." not in tts.spoken


async def test_a_clean_turn_never_flushes() -> None:
    """Flush is barge-in's alone: a turn that finishes normally must not discard audio,
    or the last words of every answer would be cut off."""
    tts = FakeTTS()
    sink = RecordingSink()
    result = await speak(
        _stream(["All done here."]), tts=tts, sink=sink, should_stop=lambda: False
    )
    assert not result.interrupted
    assert sink.flushes == 0


# --- The STT interface's own shape ------------------------------------------------


def test_a_final_carries_confidence_and_language_a_partial_does_not() -> None:
    """§B5 put `stt_confidence` and `language` on `messages`; a `Final` is where they
    come from, and a `Partial` - never stored - has no room for them."""
    final = Final(text="Guten Tag", confidence=0.94, language="de")
    assert (final.confidence, final.language) == (0.94, "de")
    partial = Partial(text="Guten")
    assert not hasattr(partial, "confidence")
