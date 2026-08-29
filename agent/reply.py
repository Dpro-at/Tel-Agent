"""What the agent says back, and the shape it has to say it in.

Milestone 0 step 2 is "it replies with a hardcoded greeting". This is that greeting -
and deliberately not a function that returns it.

**Rule 3 is why.** An agent that composes a whole answer and then sends it can never be
put on a call: the caller hears silence for the length of the generation and then a
paragraph. The fix is not something to retrofit at Milestone 11, because every interface
above it would have been written to a signature that hands back one finished string.
So the signature is an async iterator from the first line, when what it yields is a
greeting nobody had to generate.

Step 3 replaces the body of `reply` with a model call. Nothing above it changes: the
route already consumes chunks as they arrive, and already stops when the visitor goes
away.

**Cancellation is the same argument.** `cancel()` on a provider is not optional (Rule 3),
and an iterator is cancellable by construction - the consumer stops consuming and the
generator's `finally` runs. A function that returns a string has nowhere to put that.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

# Roughly a word at a time, which is what a model emits and what a person reads. Fixed
# here rather than in the route: how a reply is broken up is the generator's business,
# and the route's job is only to pass along whatever arrives.
GREETING = (
    "Hello. I am not answering for anybody yet - the model is not connected. "
    "Your message was stored, and somebody will read it."
)

# Enough delay that streaming is visibly streaming during development, and small enough
# that it is not a latency budget anybody has to think about. It goes when the model
# arrives, because then the pauses are real.
_CHUNK_DELAY = 0.04


async def reply(text: str, *, history: list[str] | None = None) -> AsyncIterator[str]:
    """The agent's answer, in the pieces it becomes available in.

    `text` and `history` are unused at step 2 and are in the signature anyway: they are
    what step 3 needs, and adding a parameter later means changing every caller at the
    moment the change is least welcome.
    """
    for index, word in enumerate(GREETING.split(" ")):
        # Cancellation lands here: when the consumer stops, this sleep is cancelled and
        # the generator unwinds. Nothing above has to know how.
        await asyncio.sleep(_CHUNK_DELAY)
        yield word if index == 0 else " " + word
