"""Taking a message — Milestone 0 step 6.

The step's own words are "it asks for name and reason, prints a structured result". The
asking is the model's, and a test that asserted on it would be testing the model. What
is this repository's - and what these tests are about - is that the result is
structured, that it is validated rather than believed, and that a failure comes back to
the model as a sentence it can act on instead of as a traceback in front of a visitor.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from agent import reply as reply_module
from agent.providers.llm.base import Message, TextDelta, ToolCall
from agent.tools import BY_NAME, TakenMessage, ToolError, parse


class Script:
    """A model that says what it was told to, in the rounds it was told to."""

    def __init__(self, *rounds: list[TextDelta | ToolCall]) -> None:
        self._rounds = list(rounds)
        self.seen: list[list[Message]] = []
        self.tools_offered: list[str] = []

    async def stream(self, messages: list[Message], tools=None) -> AsyncIterator[object]:
        # Copied, because the loop keeps appending to the same list and a reference
        # would show every round the same final state.
        self.seen.append([dict(message) for message in messages])
        self.tools_offered = [tool.name for tool in tools or []]
        for event in self._rounds.pop(0) if self._rounds else []:
            yield event


def _call(arguments: dict | str, name: str = "take_message") -> ToolCall:
    body = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(id="call-1", name=name, arguments=body)


def test_the_arguments_are_validated_rather_than_believed() -> None:
    """A model's arguments are a suggestion, and this one is refused.

    A message stored under a name the model invented sends somebody to ring the wrong
    person while the real one waits, which is worse than no message at all.
    """
    with pytest.raises(ToolError):
        parse({"reason": "the boiler is leaking"})
    with pytest.raises(ToolError):
        parse({"name": "  ", "reason": "the boiler is leaking"})


def test_what_the_caller_gave_survives_and_what_they_did_not_stays_empty() -> None:
    taken = parse({"name": " Anna Gruber ", "reason": " leaking boiler ", "urgent": True})
    assert taken == TakenMessage(
        name="Anna Gruber", reason="leaking boiler", callback=None, urgent=True
    )


def test_a_model_that_writes_an_essay_into_the_reason_is_trimmed() -> None:
    taken = parse({"name": "Anna", "reason": "x" * 900})
    assert len(taken.reason) == 500


async def test_taking_a_message_hands_the_caller_a_structured_result() -> None:
    """The step's check: a structured result, not a sentence to be read back later."""
    taken: list[TakenMessage] = []

    async def remember(message: TakenMessage) -> None:
        taken.append(message)

    provider = Script(
        [
            TextDelta("Einen Moment"),
            _call({"name": "Anna Gruber", "reason": "Heizung tropft", "callback": "0664 1"}),
        ],
        [TextDelta("Danke, jemand meldet sich.")],
    )

    said = "".join(
        [
            chunk
            async for chunk in reply_module.reply(
                "Die Heizung tropft", provider=provider, on_message_taken=remember
            )
        ]
    )

    assert taken == [
        TakenMessage(
            name="Anna Gruber", reason="Heizung tropft", callback="0664 1", urgent=False
        )
    ]
    # Both rounds reached the visitor: what the model said before the call, and the
    # confirmation after it.
    assert said == "Einen MomentDanke, jemand meldet sich."


async def test_the_model_is_told_what_the_tool_answered() -> None:
    """Without the answer going back, the model confirms a message it never took."""
    provider = Script(
        [_call({"name": "Anna", "reason": "Heizung"})],
        [TextDelta("Danke.")],
    )
    [chunk async for chunk in reply_module.reply("Die Heizung tropft", provider=provider)]

    second_round = provider.seen[1]
    assert second_round[-2]["role"] == "assistant"
    assert second_round[-2]["tool_calls"][0]["function"]["name"] == "take_message"
    answer = second_round[-1]
    assert answer["role"] == "tool"
    assert answer["tool_call_id"] == "call-1"
    assert "message was taken" in answer["content"]


async def test_the_tool_is_offered_to_the_model_at_all() -> None:
    provider = Script([TextDelta("hallo")])
    [chunk async for chunk in reply_module.reply("hallo", provider=provider)]

    assert provider.tools_offered == ["take_message"]
    assert "take_message" in BY_NAME


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (_call({"reason": "only a reason"}), "name is required"),
        (_call("{not json", "take_message"), "not valid JSON"),
        (_call({}, "book_a_flight"), "no tool called"),
    ],
    ids=["missing argument", "broken arguments", "no such tool"],
)
async def test_a_bad_call_comes_back_as_a_sentence_the_model_can_act_on(
    call: ToolCall, expected: str
) -> None:
    """A traceback here would end the turn and leave a visitor on a broken page.

    Answered instead, the model asks the visitor the question it skipped - which is the
    behaviour a person at a desk has when they realise they never got the name.
    """
    taken: list[TakenMessage] = []

    async def remember(message: TakenMessage) -> None:
        taken.append(message)

    provider = Script([call], [TextDelta("Wie war noch Ihr Name?")])
    said = "".join(
        [
            chunk
            async for chunk in reply_module.reply(
                "Hilfe", provider=provider, on_message_taken=remember
            )
        ]
    )

    assert taken == []
    assert said == "Wie war noch Ihr Name?"
    assert expected in provider.seen[1][-1]["content"]


async def test_a_model_that_only_calls_tools_forever_is_stopped() -> None:
    """The ceiling exists because the alternative is billed by the token."""
    provider = Script(*[[_call({"name": "Anna", "reason": "again"})] for _ in range(10)])
    [chunk async for chunk in reply_module.reply("hallo", provider=provider)]

    assert len(provider.seen) == reply_module.MAX_TOOL_ROUNDS
