"""The interface every language model sits behind — §B3.

    LLMProvider → stream(messages, tools) -> token stream + tool calls

One method, and it is a stream. Not a convenience: a provider that returns a finished
string cannot be put on a call, because the caller hears silence for the length of the
generation and then a paragraph (Rule 3). Writing the interface this way means the
second implementation cannot quietly be the blocking kind.

**Two kinds of thing come out of one stream.** Text to say, and calls to make. They are
separate types rather than a string with a marker in it, because the day a model emits
something that looks like a marker mid-sentence is the day a customer is read a
function call out loud.

**`cancel()` is the generator's own `aclose()`.** The specification lists `cancel()` on
the TTS interface, where audio is queued ahead of the listener and has to be thrown
away. A token stream has nothing queued: the consumer stops consuming, Python throws
`GeneratorExit` in at the `yield`, and the implementation's `finally` closes the HTTP
response — which stops the generation at the far end too. That is the whole of
cancellation here, and it is why every implementation must yield from inside a context
manager rather than collecting first.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from agent.tools import Tool


class Message(TypedDict, total=False):
    """One turn, in the vocabulary every chat model shares.

    `role` is always present. `content` is absent on the turn where the model only
    called a tool, and `tool_call_id` is present only on the turn that answers one -
    which is why this is `total=False` rather than four separate types for what the
    wire treats as one list.
    """

    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    tool_call_id: str
    name: str


@dataclass(frozen=True)
class TextDelta:
    """A piece of the answer, to be shown as it is."""

    text: str


@dataclass(frozen=True)
class ToolCall:
    """The model asking for something to be run before it finishes.

    `arguments` is the raw JSON text the model produced, not a parsed object: it is
    parsed where it is validated, and a half-streamed argument list that never
    completed must not look like an empty one that did.
    """

    id: str
    name: str
    arguments: str


Event = TextDelta | ToolCall


class LLMProvider(Protocol):
    """Streams a reply to a conversation, and the calls it wants made."""

    def stream(
        self, messages: list[Message], tools: Sequence[Tool] | None = None
    ) -> AsyncIterator[Event]:
        """The reply, in the pieces it becomes available in.

        Yields text as the model produces it, in order, and a `ToolCall` for each tool
        the model asks for. An empty stream is a valid answer to "say nothing"; an
        error is raised rather than yielded, so a caller cannot mistake a failure for a
        short reply.
        """
        ...
