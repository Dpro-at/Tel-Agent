"""What a tool is, and how the model is told about it.

A tool is three things: a name, a description the model reads to decide whether this is
the moment, and a JSON Schema for its arguments. The fourth - what it actually does -
is a coroutine, and it lives here rather than in `api/` because the model loop calls it
mid-sentence and the loop is the agent's.

**A tool never reaches the database from this package.** `agent/` does not import
`api/`, and at Milestone 11 this code runs in a process that may have no API server
beside it. What a tool produces is a value; the caller that has a session decides what
that value means - see `on_message_taken` in `agent.reply`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tool:
    """One thing the model may do, and the words it decides that by."""

    name: str
    # Read by the model, not by a person. It is the whole of what makes a tool fire at
    # the right moment, which is why it says when *not* to as well.
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]

    def as_schema(self) -> dict[str, Any]:
        """The shape a chat-completions endpoint expects to be handed."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolError(ValueError):
    """The model called a tool with arguments it cannot be run with.

    Raised rather than guessed at: a message taken under the wrong name is worse than
    no message, because somebody rings the wrong person back and the real one is never
    called. The loop turns this into a sentence the model can act on and try again.
    """
