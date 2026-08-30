"""A model behind an OpenAI-compatible `/chat/completions` endpoint.

The one implementation v1 ships (§B3: *implement exactly one of each*). It is written
against the API *shape* rather than one company's service, because that shape is what a
hosted gateway, a self-hosted server and a laptop running a local model all speak —
which is the difference between "swap the provider" being a configuration change and
being a rewrite. `LLM_BASE_URL` is the whole of that swap.

**Every token is yielded from inside the open response.** Collecting the stream and
returning it would satisfy the type and break Rule 3; worse, it would make cancellation
impossible, because there would be nothing left to close when the visitor leaves.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from agent.config import LlmSettings
from agent.providers.llm.base import Message

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
    """Streams tokens from a chat-completions endpoint."""

    def __init__(
        self, settings: LlmSettings, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        # Injectable so a test can hand in a transport instead of reaching the network.
        # A caller passing a client keeps ownership of it: closing somebody else's
        # client is how a shared connection pool dies halfway through a call.
        self._client = client

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        """The model's reply, token by token, in the order it is produced."""
        payload = {
            "model": self._settings.model,
            "messages": messages,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self._settings.api_key}"}
        url = f"{self._settings.base_url}/chat/completions"

        if self._client is not None:
            async for chunk in self._read(self._client, url, payload, headers):
                yield chunk
            return

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async for chunk in self._read(client, url, payload, headers):
                yield chunk

    async def _read(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict,
        headers: dict[str, str],
    ) -> AsyncIterator[str]:
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
                fragment = _fragment(line)
                if fragment is None:
                    continue
                if fragment == "":
                    # A chunk that carries a role and no text - the opening one usually
                    # does. Skipping it keeps "a chunk arrived" meaning "there is
                    # something to show".
                    continue
                yield fragment


def _fragment(line: str) -> str | None:
    """The text in one server-sent event, or `None` when the line carries none.

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
    if not isinstance(delta, dict):
        return None

    content = delta.get("content")
    return content if isinstance(content, str) else None
