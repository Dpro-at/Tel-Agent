"""The model behind the reply — Milestone 0 step 3.

The tests that matter here are not "does it return text". They are the three properties
Rule 3 says the phone will need and that are cheap to get right now: the tokens arrive
as they are produced, stopping the consumer stops the generation, and an installation
with no model configured still answers something true.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from agent import reply as reply_module
from agent.config import ConfigurationError, LlmSettings, llm_settings
from agent.providers.llm import Message, configured_provider, provider_for
from agent.providers.llm.base import TextDelta
from agent.providers.llm.openai_compatible import OpenAICompatibleLLM

SETTINGS = LlmSettings(
    provider="openai", model="a-model", api_key="a-key", base_url="https://model.test/v1"
)


def _event(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n"


def _stream_body(*parts: str) -> bytes:
    # The opening chunk of a real stream carries a role and no content, and the last
    # line is not JSON at all. Both are in here because both are what breaks a parser.
    opening = 'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
    return (opening + "".join(_event(part) for part in parts) + "data: [DONE]\n\n").encode()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_tokens_arrive_in_the_order_the_model_produced_them() -> None:
    async with _client(
        lambda request: httpx.Response(200, content=_stream_body("Guten ", "Tag", "."))
    ) as client:
        provider = OpenAICompatibleLLM(SETTINGS, client=client)
        events = [event async for event in provider.stream([{"role": "user", "content": "hi"}])]

    # Three pieces, not one string: a provider that buffered would pass an equality
    # check on the joined text and fail this line, which is the point of it.
    assert [event.text for event in events] == ["Guten ", "Tag", "."]


async def test_the_request_carries_the_model_the_key_and_the_turns() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_stream_body("ok"))

    turns: list[Message] = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hallo"},
    ]
    async with _client(handler) as client:
        provider = OpenAICompatibleLLM(SETTINGS, client=client)
        [event async for event in provider.stream(turns)]

    request = seen[0]
    assert str(request.url) == "https://model.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer a-key"
    body = json.loads(request.content)
    assert body["model"] == "a-model"
    assert body["stream"] is True
    assert body["messages"] == turns


async def test_a_refusal_is_raised_rather_than_returned_as_a_short_reply() -> None:
    async with _client(lambda request: httpx.Response(401, json={"error": "no"})) as client:
        provider = OpenAICompatibleLLM(SETTINGS, client=client)
        with pytest.raises(httpx.HTTPStatusError):
            [event async for event in provider.stream([{"role": "user", "content": "hi"}])]


async def test_a_chunk_that_makes_no_sense_shortens_the_reply_rather_than_breaking_it() -> None:
    body = (
        "data: not json at all\n\n"
        + _event("still ")
        + 'data: {"choices":[]}\n\n'
        + _event("here")
        + "data: [DONE]\n\n"
    ).encode()

    async with _client(lambda request: httpx.Response(200, content=body)) as client:
        provider = OpenAICompatibleLLM(SETTINGS, client=client)
        events = [event async for event in provider.stream([{"role": "user", "content": "hi"}])]

    assert [event.text for event in events] == ["still ", "here"]


async def test_stopping_the_consumer_closes_the_stream() -> None:
    """Rule 3's cancellation, in the shape a token stream has it.

    Nothing here asserts on a `cancel()` method: the consumer stops, the generator's
    `finally` closes the response, and the far end stops generating. What is asserted
    is that this happens without an exception and without the rest of the answer being
    read - a provider that buffered would have read all of it before yielding.
    """
    async with _client(
        lambda request: httpx.Response(200, content=_stream_body("one", "two", "three"))
    ) as client:
        provider = OpenAICompatibleLLM(SETTINGS, client=client)
        stream = provider.stream([{"role": "user", "content": "hi"}])
        first = await anext(stream)
        await stream.aclose()

    assert first == TextDelta("one")


async def test_no_model_configured_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    llm_settings.cache_clear()
    try:
        assert configured_provider() is None
        answer = "".join([chunk async for chunk in reply_module.reply("hallo")])
    finally:
        llm_settings.cache_clear()

    assert answer == reply_module.GREETING


async def test_half_a_configuration_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider with no key behind it is somebody who has not finished.

    Falling back to the greeting here would look exactly like the model answering
    badly, and the person who set `LLM_PROVIDER` would have no way to tell.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm_settings.cache_clear()
    try:
        with pytest.raises(ConfigurationError) as refused:
            llm_settings()
    finally:
        llm_settings.cache_clear()

    assert "LLM_MODEL" in str(refused.value)


async def test_a_provider_this_build_does_not_have_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "a-service-that-does-not-exist")
    llm_settings.cache_clear()
    try:
        with pytest.raises(ConfigurationError):
            llm_settings()
    finally:
        llm_settings.cache_clear()


async def test_the_configuration_names_the_implementation() -> None:
    assert isinstance(provider_for(SETTINGS), OpenAICompatibleLLM)


async def test_the_reply_asks_the_model_to_answer_in_the_visitors_language() -> None:
    """Step 3's whole sentence: *in the visitor's language*.

    Asserted on the request rather than on an answer, because what a model does with an
    instruction is the model's business and what this repository controls is whether it
    was given one.
    """
    asked: list[list[Message]] = []

    class Recorder:
        async def stream(self, messages: list[Message], tools=None) -> AsyncIterator[TextDelta]:
            asked.append(messages)
            yield TextDelta("servus")

    reply_module_provider = Recorder()
    original = reply_module.configured_provider
    reply_module.configured_provider = lambda: reply_module_provider  # type: ignore[assignment]
    try:
        history: list[Message] = [
            {"role": "user", "content": "seid ihr am Samstag offen?"},
            {"role": "assistant", "content": "Ja, bis 14 Uhr."},
        ]
        answer = "".join(
            [chunk async for chunk in reply_module.reply("und am Sonntag?", history=history)]
        )
    finally:
        reply_module.configured_provider = original  # type: ignore[assignment]

    assert answer == "servus"
    messages = asked[0]
    assert messages[0]["role"] == "system"
    assert "language the visitor wrote in" in messages[0]["content"]
    # The thread, in order, with the new line last. A model that is handed the turns
    # out of order answers the wrong question, and nothing else would notice.
    assert [message["content"] for message in messages[1:]] == [
        "seid ihr am Samstag offen?",
        "Ja, bis 14 Uhr.",
        "und am Sonntag?",
    ]
