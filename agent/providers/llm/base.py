"""The interface every language model sits behind — §B3.

One method, and it is a stream. Not a convenience: a provider that returns a finished
string cannot be put on a call, because the caller hears silence for the length of the
generation and then a paragraph (Rule 3). Writing the interface this way means the
second implementation cannot quietly be the blocking kind.

**`cancel()` is the generator's own `aclose()`.** The specification lists `cancel()` on
the TTS interface, where audio is queued ahead of the listener and has to be thrown
away. A token stream has nothing queued: the consumer stops consuming, Python throws
`GeneratorExit` in at the `yield`, and the implementation's `finally` closes the HTTP
response — which stops the generation at the far end too. That is the whole of
cancellation here, and it is why every implementation must yield from inside a context
manager rather than collecting first.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal, Protocol, TypedDict


class Message(TypedDict):
    """One turn. The vocabulary every chat model shares, and nothing beyond it."""

    role: Literal["system", "user", "assistant"]
    content: str


class LLMProvider(Protocol):
    """Streams a reply to a conversation."""

    def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        """The reply, in the pieces it becomes available in.

        Yields text fragments as the model produces them, in order. An empty stream is
        a valid answer to "say nothing"; an error is raised rather than yielded, so a
        caller cannot mistake a failure for a short reply.
        """
        ...
