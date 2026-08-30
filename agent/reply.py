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
import logging
from collections.abc import AsyncIterator, Sequence

from agent.providers.llm import Message, configured_provider

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


async def reply(text: str, *, history: Sequence[Message] | None = None) -> AsyncIterator[str]:
    """The agent's answer, in the pieces it becomes available in.

    `history` is the thread so far in the model's own vocabulary - `user` for the
    visitor, `assistant` for the agent - oldest first, without the system prompt. It is
    the caller's job to map its own words for those two onto these, because the caller
    is the one that knows what a speaker column means.
    """
    provider = configured_provider()
    if provider is None:
        async for word in _greeting():
            yield word
        return

    # Deliberately not caught here. A model that fails mid-sentence must not leave half
    # an answer behind: the route stores the reply only when the stream ends normally,
    # so letting this raise is what keeps a broken generation out of the transcript.
    async for chunk in provider.stream(_conversation(text, history)):
        yield chunk
