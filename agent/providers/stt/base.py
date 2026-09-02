"""The interface every speech-to-text provider sits behind — §B3.

    STTProvider → stream(audio) -> partial and final transcripts

One method, and it is a stream in and a stream out. Audio arrives in chunks as the
caller speaks, and transcripts come back before the caller has stopped: a **partial**
is the provider's best guess so far, revised as more audio arrives, and a **final** is
what it commits to once it decides a phrase has ended.

**Partials are what make the budget.** Rule 3 gives endpointing — deciding the caller
has finished — the largest single slice of the 800 ms, and a provider that only spoke
at end of utterance would spend that slice in silence. Partials let the turn-taker see
the sentence forming and start thinking before the caller's mouth has closed. They are
never stored and never read aloud; only a final becomes a transcript line.

**A final carries its confidence and language, because §B5 asked for them per line.**
`messages.stt_confidence` and `.language` turn "German accuracy" (Rule 4) from an
impression into a query, and they are null on a text channel — which is itself the
signal that a line was typed rather than spoken. So they are on the `Final`, not
guessed later.

**Cancellation is the stream's own close**, the same argument the LLM interface makes:
the consumer stops consuming, `GeneratorExit` is thrown in at the `yield`, and the
implementation's `finally` closes the provider's socket. An implementation must
therefore yield from inside its connection's context manager rather than collecting
first — the interface is written so the second implementation cannot quietly be the
blocking kind.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Partial:
    """The provider's best guess at the phrase in progress, still being revised.

    Not stored, not spoken: a partial exists so the turn-taker can see the caller is
    still talking and can begin work before the final arrives. Its text will change.
    """

    text: str


@dataclass(frozen=True)
class Final:
    """A phrase the provider has committed to — this becomes one transcript line.

    `confidence` is 0..1 where the provider offers it, else None; `language` is the
    BCP-47 tag it detected, or None. Both ride here rather than being inferred later,
    because §B5 put `stt_confidence` and `language` on `messages` for exactly this and
    the provider is the only thing that knows them.
    """

    text: str
    confidence: float | None = None
    language: str | None = None


Transcript = Partial | Final


class STTProvider(Protocol):
    """Turns a stream of audio into a stream of transcripts."""

    def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[Transcript]:
        """Transcribe `audio` as it arrives, yielding partials then finals.

        Consumes audio chunks (the codec is the transport's concern, agreed out of
        band — G.711 8 kHz mono for v1, per the SIP settings) and yields `Partial`s as
        the guess firms up and a `Final` when a phrase closes. A silent call yields
        nothing and is not an error. An error is raised, never yielded, so a caller
        cannot mistake a failed connection for a caller who said nothing.
        """
        ...
