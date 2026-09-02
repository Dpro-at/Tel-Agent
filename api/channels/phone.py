"""The phone, from the archive's side — Milestone 11, §B5 decision 6.

A call is a conversation on a `phone` channel plus a `calls` row for what only a call
has: the caller's number, the recording, the billable seconds, the provider cost. This
module is the driver that turns a live call into those rows and back: it consumes a
stream of transcripts, answers each with the voice core (`agent/session/turn.py`), and
stores the whole thing where `/api/conversations/search` can find it beside every chat.

**The transport boundary is the transcript, not the audio.** The SIP/LiveKit transport
owns codec ↔ STT ↔ TTS and the room; it hands this driver a `CallTransport` — a stream
of `Partial`/`Final`s, a `TTSProvider`, and an `AudioSink` to speak into. That seam is
what keeps the archive side testable without a line, a number or a provider key: a
scripted transport stands in for a caller, and the real one implements the same three
things. It is also what §B10's self-hosted-media requirement rests on — the transport
is swappable because nothing above it knows which one it is.

**Turn-taking is a race, and barge-in is the point of it.** While the agent speaks, the
driver is still reading the transcript stream; a new transcript arriving mid-answer is
the caller cutting in, and it stops the speaking (`agent.session.speak`'s `should_stop`)
and stores only what was actually said — a stored line nobody heard is a transcript
lying, the same rule every push channel already holds.

**Routing runs first, on the caller ID** — Milestone 4's engine, the phone half it was
built for. A blocked number is never answered and leaves no conversation; a `pass`
number is handed to a person and the agent stays silent. This is the one place the
caller-ID question (§A2, settled on the live line) actually bites, and it is wired now
so that when the number is real the behaviour already exists.

`agent/` may not import `api/`, so the voice core knows nothing of these tables: it
speaks and measures, and this driver — which has a session — decides what that means.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import time
from collections.abc import AsyncIterator
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from agent.config import ConfigurationError
from agent.providers.stt.base import Final, Transcript
from agent.providers.tts import TTSProvider
from agent.reply import reply as generate_reply
from agent.session import AudioSink, TurnResult, speak
from api import llm, routing, webhooks
from api.conversations import position_ms
from api.db import create_sessionmaker, session_scope
from api.models import Call, Channel, Conversation, Message

logger = logging.getLogger("api.phone")


class CallTransport(Protocol):
    """One live call, as the archive side needs to see it.

    The SIP/LiveKit transport implements this; a scripted one stands in for tests. It
    is deliberately small: the driver does not touch audio or a codec, only transcripts
    in and speech out.
    """

    from_e164: str | None
    tts: TTSProvider
    sink: AudioSink

    def transcripts(self) -> AsyncIterator[Transcript]:
        """The caller's speech as it is recognised — partials then finals, until the
        call ends and the stream closes."""
        ...


async def _open_call(
    db: DbSession, channel: Channel, from_e164: str | None
) -> tuple[Conversation, Call]:
    """A conversation on the phone channel and its calls row, committed together."""
    conversation = Conversation(
        workspace_id=channel.workspace_id,
        channel_id=channel.id,
        direction="inbound",
        external_id=from_e164,
        handling="ai",
        status="open",
    )
    db.add(conversation)
    await db.flush()
    call = Call(
        conversation_id=conversation.id,
        workspace_id=channel.workspace_id,
        from_e164=from_e164,
    )
    db.add(call)
    await db.commit()
    await db.refresh(conversation)
    return conversation, call


async def _store_line(
    db: DbSession,
    conversation: Conversation,
    *,
    speaker: str,
    text: str,
    confidence: float | None = None,
    language: str | None = None,
) -> Message:
    line = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        ts_ms=position_ms(conversation.started_at),
        speaker=speaker,
        text=text,
        stt_confidence=confidence,
        language=language,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


async def _announce_start(db: DbSession, channel: Channel, conversation: Conversation) -> None:
    """The same `conversation.started` every channel queues, so a phone call is not a
    special case to a webhook receiver — the only difference §B13 allows is transport."""
    try:
        await webhooks.queue(
            db,
            workspace_id=channel.workspace_id,
            event="conversation.started",
            data={
                "conversation": conversation.external_id,
                "channel": "phone",
                "started_at": conversation.started_at.isoformat()
                if conversation.started_at
                else None,
            },
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "could not queue call-started webhook",
            extra={"conversation_id": conversation.id},
        )


async def _answer(
    db: DbSession,
    channel: Channel,
    conversation: Conversation,
    caller_text: str,
    transport: CallTransport,
    incoming: Message,
    provider_override: object | None,
) -> tuple[asyncio.Task, asyncio.Event]:
    """Start speaking the agent's answer, returning the speak task and its stop event.

    The reply is generated with the same history and tools every channel uses; the
    difference is only that it is spoken rather than sent. The caller of this decides
    the race against the next transcript.
    """
    provider = provider_override
    if provider is None:
        try:
            provider = await llm.resolve_provider(db)
        except ConfigurationError:
            logger.exception(
                "phone could not resolve a model", extra={"channel_id": channel.id}
            )
            provider = None

    from api.agent_tools import toolset
    from api.routes.public_chat import _history

    history = await _history(db, conversation, incoming)
    tools = toolset(
        create_sessionmaker(db.bind),
        workspace_id=channel.workspace_id,
        conversation_id=conversation.id,
    )

    barge = asyncio.Event()
    since = time.perf_counter()
    task = asyncio.ensure_future(
        speak(
            generate_reply(caller_text, provider=provider, history=history, tools=tools),
            tts=transport.tts,
            sink=transport.sink,
            should_stop=barge.is_set,
            since=since,
        )
    )
    return task, barge


def _log_turn(conversation: Conversation, result: TurnResult) -> None:
    if result.first_audio_ms is not None:
        # Rule 4: end of caller speech to first audio out, the number this whole
        # product was built to keep small.
        logger.info(
            "call turn",
            extra={
                "conversation_id": conversation.id,
                "first_audio_ms": round(result.first_audio_ms),
                "interrupted": result.interrupted,
            },
        )


async def run_call(
    sessionmaker: async_sessionmaker,
    *,
    channel_id: int,
    transport: CallTransport,
    provider: object | None = None,
) -> int | None:
    """Drive one phone call end to end, returning its conversation id.

    Returns None when the caller was blocked by a rule and no conversation was opened.
    The call ends when the transcript stream closes (the caller hung up); the calls row
    is finalised with its billable seconds and the conversation is closed.

    `provider` overrides the model, for the standalone agent process that resolves its
    own and for tests; omitted, the model is resolved from settings like every channel.
    """
    async with session_scope(sessionmaker) as db:
        channel = await db.get(Channel, channel_id)
        if channel is None:
            logger.warning("call for an unknown channel %s", channel_id)
            return None

        # Milestone 4, on the caller ID. A blocked number is never answered.
        decision = await routing.decide(
            db,
            workspace_id=channel.workspace_id,
            identities=[transport.from_e164] if transport.from_e164 else [],
        )
        if decision.action == "block":
            logger.info(
                "call dropped by rule",
                extra={"channel_id": channel_id, "pattern": decision.pattern},
            )
            return None

        conversation, call = await _open_call(db, channel, transport.from_e164)
        await _announce_start(db, channel, conversation)
        if decision.action == "pass":
            await routing.apply_pass(db, conversation, decision)

        started_at = conversation.started_at or dt.datetime.now(dt.UTC)
        answering = decision.action == "ai"

        # A single pull kept in flight, so the driver can race the next transcript
        # against the agent's speaking - which is what makes barge-in real.
        stream = transport.transcripts().__aiter__()
        pending = asyncio.ensure_future(_next(stream))
        carried: Transcript | None = None

        try:
            while True:
                if carried is not None:
                    transcript, carried = carried, None
                else:
                    transcript = await pending
                    pending = asyncio.ensure_future(_next(stream))

                if transcript is None:
                    break
                if not isinstance(transcript, Final):
                    # A partial before any answer is just the caller still talking.
                    continue

                caller_line = await _store_line(
                    db,
                    conversation,
                    speaker="caller",
                    text=transcript.text,
                    confidence=transcript.confidence,
                    language=transcript.language,
                )

                if not answering:
                    # A person has the call (a `pass` rule, or a takeover). The agent is
                    # silent; the caller's words are still recorded.
                    continue

                task, barge = await _answer(
                    db, channel, conversation, transcript.text, transport, caller_line, provider
                )
                done, _ = await asyncio.wait(
                    {task, pending}, return_when=asyncio.FIRST_COMPLETED
                )

                if pending in done and pending.result() is not None:
                    # The caller spoke while the agent was speaking: barge-in. Stop the
                    # answer and store only what was said - a stored line nobody heard
                    # is a transcript lying.
                    interrupter = pending.result()
                    pending = asyncio.ensure_future(_next(stream))
                    barge.set()
                    result = await task
                    _log_turn(conversation, result)
                    if result.text:
                        await _store_line(db, conversation, speaker="agent", text=result.text)
                    # A Final interrupter is the next caller turn; a partial is the
                    # caller still forming one, and the loop reads on for its final.
                    carried = interrupter if isinstance(interrupter, Final) else None
                else:
                    # Either the answer finished on its own, or the caller stopped
                    # talking (the stream ended) - which is not barge-in, so the answer
                    # is allowed to complete before the call is closed below.
                    result = await task
                    _log_turn(conversation, result)
                    if result.text:
                        await _store_line(db, conversation, speaker="agent", text=result.text)
                    if pending.done() and pending.result() is None:
                        break
        finally:
            if not pending.done():
                pending.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending

        ended_at = dt.datetime.now(dt.UTC)
        conversation.status = "closed"
        conversation.ended_at = ended_at.replace(tzinfo=None)
        call.billable_seconds = max(0, int((ended_at - _aware(started_at)).total_seconds()))
        await db.commit()
        logger.info(
            "call ended",
            extra={
                "conversation_id": conversation.id,
                "billable_seconds": call.billable_seconds,
            },
        )
        return conversation.id


async def _next(stream: AsyncIterator[Transcript]) -> Transcript | None:
    """The next transcript, or None when the caller has hung up."""
    try:
        return await stream.__anext__()
    except StopAsyncIteration:
        return None


def _aware(value: dt.datetime) -> dt.datetime:
    """A stored naive timestamp as UTC, so a subtraction does not raise."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)
