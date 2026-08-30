"""Built-in tools the model can invoke."""

from __future__ import annotations

from agent.tools.base import Tool, ToolError
from agent.tools.take_message import TAKE_MESSAGE, TakenMessage, parse

# The tools every conversation is offered. A list rather than a registry with
# registration calls: v1 has one, and a list of one is honest about that. When a channel
# needs a different set, this becomes a function of the channel and the call sites do
# not change, because they already ask for "the tools" rather than naming them.
BUILTIN: list[Tool] = [TAKE_MESSAGE]

BY_NAME: dict[str, Tool] = {tool.name: tool for tool in BUILTIN}

__all__ = ["BUILTIN", "BY_NAME", "TAKE_MESSAGE", "TakenMessage", "Tool", "ToolError", "parse"]
