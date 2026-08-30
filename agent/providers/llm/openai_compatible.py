"""A model behind an OpenAI-compatible `/chat/completions` endpoint.

The one implementation v1 ships (§B3: *implement exactly one of each*). It is written
against the API *shape* rather than one company's service, because that shape is what a
hosted gateway, a self-hosted server and a laptop running a local model all speak —
which is the difference between "swap the provider" being a configuration change and
being a rewrite. `LLM_BASE_URL` is the whole of that swap.

**Every token is yielded from inside the open response.** Collecting the stream and
returning it would satisfy the type and break Rule 3; worse, it would make cancellation
impossible, because there would be nothing left to close when the visitor leaves.

**A tool call is the one thing here that cannot be streamed through.** Its arguments
arrive as JSON in fragments, and half of a JSON object is not a smaller JSON object -
so those fragments are joined and the call is emitted once it is whole. Text is never
held back for it: an answer that mentions taking a message is said while the call to
take it is still being assembled.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from agent.config import LlmSettings
from agent.providers.llm.base import Event, Message, TextDelta, ToolCall
from agent.tools import Tool

logger = logging.getLogger("agent.llm")

# The first token is the number Rule 3 budgets (~250 ms); the rest arrive after it and
# a slow paragraph is still a paragraph a person is already reading. So the connect and
# read deadlines are tight and the total is not bounded here at all - a long answer is
# not a hung one, and the consumer cancels when it stops caring.
TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)

# What ends the stream. Sent as a literal string rather than as JSON, which is the one
# place this format is not JSON at all.
DONE = "[DONE]"


class OpenAICompatibleLLM:
    """Streams tokens and tool calls from a chat-completions endpoint."""

    def __init__(
        self, settings: LlmSettings, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        # Injectable so a test can hand in a transport instead of reaching the network.
        # A caller passing a client keeps ownership of it: closing somebody else's
        # client is how a shared connection pool dies halfway through a call.
        self._client = client

    async def stream(
        self, messages: list[Message], tools: Sequence[Tool] | None = None
    ) -> AsyncIterator[Event]:
        """The model's reply, in the order it is produced."""
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            payload["tools"] = [tool.as_schema() for tool in tools]

        headers = {"Authorization": f"Bearer {self._settings.api_key}"}
        url = f"{self._settings.base_url}/chat/completions"

        if self._client is not None:
            async for event in self._read(self._client, url, payload, headers):
                yield event
            return

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async for event in self._read(client, url, payload, headers):
                yield event

    async def _read(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> AsyncIterator[Event]:
        # Keyed by the index the wire uses, because a model may assemble two calls at
        # once and the fragments of one carry no other way of telling them apart.
        pending: dict[int, dict[str, str]] = {}

        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                # The body has to be read before it can be quoted: on a streaming
                # request httpx has not fetched it yet, and the exception would carry
                # nothing but a number.
                await response.aread()
                logger.warning(
                    "the model refused the request",
                    extra={"status": response.status_code},
                )
                response.raise_for_status()

            async for line in response.aiter_lines():
                delta = _delta(line)
                if delta is None:
                    continue

                content = delta.get("content")
                if isinstance(content, str) and content:
                    # A chunk carrying only a role - the opening one usually does -
                    # falls through here, which keeps "an event arrived" meaning "there
                    # is something to show".
                    yield TextDelta(content)

                for fragment in delta.get("tool_calls") or []:
                    _accumulate(pending, fragment)

        for call in pending.values():
            if call["name"]:
                yield ToolCall(id=call["id"], name=call["name"], arguments=call["arguments"])


def _accumulate(pending: dict[int, dict[str, str]], fragment: Any) -> None:
    """Join one fragment of a tool call onto the call it belongs to."""
    if not isinstance(fragment, dict):
        return
    index = fragment.get("index", 0)
    if not isinstance(index, int):
        return

    call = pending.setdefault(index, {"id": "", "name": "", "arguments": ""})
    identifier = fragment.get("id")
    if isinstance(identifier, str) and identifier:
        call["id"] = identifier

    function = fragment.get("function")
    if not isinstance(function, dict):
        return
    name = function.get("name")
    if isinstance(name, str) and name:
        call["name"] = name
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        call["arguments"] += arguments


def _delta(line: str) -> dict[str, Any] | None:
    """The delta object in one server-sent event, or `None` when it carries none.

    Kept apart from the reading loop because this is the part that differs between
    services claiming the same format - and a parser that returns `None` for anything
    it does not recognise degrades to a shorter reply rather than to a traceback in
    front of a customer.
    """
    if not line.startswith("data:"):
        return None

    body = line[len("data:") :].strip()
    if not body or body == DONE:
        return None

    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("unreadable chunk from the model")
        return None

    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    delta = choices[0].get("delta")
    return delta if isinstance(delta, dict) else None
