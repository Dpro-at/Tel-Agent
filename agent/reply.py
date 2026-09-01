"""What the agent says back, and the shape it has to say it in.

**Rule 3 is why this is an iterator.** An agent that composes a whole answer and then
sends it can never be put on a call: the caller hears silence for the length of the
generation and then a paragraph. The fix is not something to retrofit at Milestone 11,
because every interface above it would have been written to a signature that hands back
one finished string. So the signature was an async iterator from the first line, when
what it yielded was a greeting nobody had to generate.

Milestone 0 step 3 is that model call. Nothing above this file changed to get it: the
route already consumed chunks as they arrived, and already stopped when the visitor went
away.

**Cancellation is the same argument.** `cancel()` on a provider is not optional (Rule 3),
and an iterator is cancellable by construction - the consumer stops consuming, the
generator's `finally` runs, and the provider's open response closes, which stops the
generation at the far end rather than only hiding it.

**An installation with no model still answers.** It says so, in words, instead of
failing a request the visitor can do nothing about. That is the greeting below, and it
is the honest state of a fresh install rather than a placeholder left behind.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

from agent.providers.llm import LLMProvider, Message
from agent.providers.llm.base import TextDelta, ToolCall
from agent.tools import BUILTIN, TakenMessage, Tool, ToolError
from agent.tools import parse as parse_taken_message

logger = logging.getLogger("agent.reply")

# What the model is, and the one instruction the roadmap's step 3 is written around:
# the reply appears **in the visitor's language**. Not a language setting - a visitor
# who writes in German gets German, and the same widget serves the next person who
# writes in Turkish. `STT_LANGUAGE` exists for the phone, where there is no text to
# read the language from; here there is.
SYSTEM_PROMPT = (
    "You answer for a small business, in a chat window on its website. "
    "Reply in the language the visitor wrote in, and match its register. "
    "Be brief: two or three sentences, the way somebody at a desk would answer. "
    "Never invent prices, opening hours, availability or anything else specific to "
    "this business. When you do not know, say so plainly and offer to take a message."
)

# How many times the model may call tools before the turn ends. Three is two more than
# any answer has needed: it is a ceiling on a loop that a confused model can otherwise
# ride forever, at the visitor's expense and the account's.
MAX_TOOL_ROUNDS = 3

# What is done with a taken message differs per channel - a tray for web chat, and at
# Milestone 11 something a person hears about while the caller is still on the line. So
# the agent produces the value and the caller decides, which is also what keeps this
# package free of `api/`.
MessageTaken = Callable[[TakenMessage], Awaitable[None]]

# Roughly a word at a time, which is what a model emits and what a person reads.
GREETING = (
    "Hello. I am not answering for anybody yet - the model is not connected. "
    "Your message was stored, and somebody will read it."
)

# Enough delay that streaming is visibly streaming while no model is connected, and
# small enough that it is not a latency budget anybody has to think about. It applies
# only to the greeting: once the model answers, the pauses are real.
_CHUNK_DELAY = 0.04


async def _greeting() -> AsyncIterator[str]:
    for index, word in enumerate(GREETING.split(" ")):
        # Cancellation lands here: when the consumer stops, this sleep is cancelled and
        # the generator unwinds. Nothing above has to know how.
        await asyncio.sleep(_CHUNK_DELAY)
        yield word if index == 0 else " " + word


def _conversation(text: str, history: Sequence[Message] | None) -> list[Message]:
    """The turns the model is asked to continue, oldest first.

    The system prompt is rebuilt on every call rather than stored with the thread: it
    is this build's instructions, and a conversation resumed after an upgrade must be
    answered by the new ones.
    """
    messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": text})
    return messages


async def _run(
    call: ToolCall, by_name: dict[str, Tool], on_message_taken: MessageTaken | None
) -> str:
    """One tool call, and the sentence the model is handed back.

    Every failure is answered rather than raised. A model that is told "that is not a
    tool" or "name is required" asks the visitor the question it skipped; a model whose
    turn ends in a traceback leaves somebody mid-conversation with a broken page.
    """
    tool = by_name.get(call.name)
    if tool is None:
        logger.warning("the model called a tool that does not exist", extra={"tool": call.name})
        return f"There is no tool called {call.name!r}."

    try:
        arguments = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        return "Those arguments were not valid JSON. Try the call again."
    if not isinstance(arguments, dict):
        return "Arguments must be an object."

    try:
        answer = await tool.run(arguments)
    except ToolError as refused:
        return str(refused)

    if tool.name == "take_message" and on_message_taken is not None:
        # Parsed a second time on purpose: the tool validated the arguments for itself,
        # and this is the value the caller stores. Sharing a mutable result between the
        # two would make the tool's contract depend on who called it.
        await on_message_taken(parse_taken_message(arguments))

    return answer


async def reply(
    text: str,
    *,
    provider: LLMProvider | None = None,
    history: Sequence[Message] | None = None,
    on_message_taken: MessageTaken | None = None,
    tools: Sequence[Tool] | None = None,
) -> AsyncIterator[str]:
    """The agent's answer, in the pieces it becomes available in.

    **`provider` is passed in, not looked up.** Since §B9.2 the key can live in the
    database, and the database is on the far side of the boundary this package may not
    cross - so whoever *can* read the source resolves it and hands the result down.
    `api.llm.resolve_provider` is that reader where a dashboard exists;
    `agent.providers.llm.configured_provider` is it where one does not, which is the
    standalone process at Milestone 11.

    `None` therefore means what it says: this installation has no model, and the
    greeting below is spoken. It is not a request to go and look for one.

    `history` is the thread so far in the model's own vocabulary - `user` for the
    visitor, `assistant` for the agent - oldest first, without the system prompt. It is
    the caller's job to map its own words for those two onto these, because the caller
    is the one that knows what a speaker column means.
    """
    if provider is None:
        async for word in _greeting():
            yield word
        return

    # The channel decides what the model may do (§B7): the caller hands the set in,
    # already bound to its conversation, and the default is the channel-free minimum.
    offered = list(tools) if tools is not None else list(BUILTIN)
    by_name = {tool.name: tool for tool in offered}

    messages = _conversation(text, history)

    for _round in range(MAX_TOOL_ROUNDS):
        spoken: list[str] = []
        calls: list[ToolCall] = []

        # Deliberately not caught. A model that fails mid-sentence must not leave half
        # an answer behind: the route stores the reply only when the stream ends
        # normally, so letting this raise keeps a broken generation out of the
        # transcript.
        async for event in provider.stream(messages, offered):
            if isinstance(event, TextDelta):
                spoken.append(event.text)
                yield event.text
            else:
                calls.append(event)

        if not calls:
            return

        # The turn the model just took, tool calls and all, has to go back to it or the
        # answers below have nothing to answer.
        asked: Message = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in calls
            ],
        }
        if spoken:
            asked["content"] = "".join(spoken)
        messages.append(asked)

        for call in calls:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": await _run(call, by_name, on_message_taken),
                }
            )

    logger.warning(
        "the model kept calling tools; the turn was ended", extra={"text": text[:80]}
    )
