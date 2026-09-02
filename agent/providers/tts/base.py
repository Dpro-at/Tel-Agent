"""The interface every text-to-speech provider sits behind — §B3.

    TTSProvider → stream(text) -> audio chunks
                → cancel()        # on barge-in, stop instantly

**`cancel()` is not optional, and it is the whole reason this milestone is hard.** When
the caller talks over the agent, the audio already produced is sitting in a playback
buffer ahead of the listener, and stopping the *producer* is not enough — the queued
speech has to be thrown away too, or the agent finishes its sentence over the caller
and the product feels broken (Rule 3). So cancellation here is two acts, and the
interface names both:

- **Stop producing.** Closing the returned audio iterator throws `GeneratorExit` into
  the provider's generator, whose `finally` closes the HTTP response — which stops the
  synthesis at the far end rather than only ignoring it, so nothing more is billed.
- **Flush what is queued.** That is the *sink's* job, not the provider's: the thing
  playing the audio holds the buffer, and `agent/session` flushes it on barge-in. The
  provider cannot flush a buffer it never sees.

This module is the producer half. It is written as an async iterator for the same
reason the LLM interface is: a provider that returned a finished audio blob could never
be put on a call, because the first chunk would not leave until the last was made
(Rule 3 — the first sentence speaks while the rest is still being generated). Writing
the signature this way means the second implementation cannot quietly be the blocking
kind.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class TTSProvider(Protocol):
    """Turns text into a stream of audio chunks, cancellable mid-utterance."""

    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Speak `text`, yielding audio chunks as they are synthesised.

        The first chunk must leave as soon as it exists, not once the whole utterance
        is made: on a call the first sentence is spoken while the rest is still being
        generated. Chunks are in the codec the transport expects (G.711 8 kHz mono for
        v1). Cancellation is closing this iterator — the generator's `finally` closes
        the provider's response, stopping synthesis at the far end — paired with the
        sink flushing whatever it has already buffered. An error is raised, not yielded.
        """
        ...
