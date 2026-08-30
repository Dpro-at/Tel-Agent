"""The web chat widget's endpoint — §B14, and the only unauthenticated write in the product.

Everything else here is reached with a session. This is not: the address travels in the
HTML of a public page, so anybody who reads that page can call it. What stands in the way
is the origin allowlist (`api/security/embed.py`), and the rule that governs every line
below is §B14's:

    Every response this endpoint can produce must be safe to show a stranger.

That is why a channel that does not exist, one that is disabled, and one whose allowlist
refuses the caller all answer the same way. Distinguishing them would turn the address
into an oracle: paste it anywhere and learn whether a business runs Tel-Agent, and
whether their widget is switched on.

Milestone 0's step 0 and step 1 (`docs/ROADMAP.md`): refuse the wrong origin, and let the
right one's message arrive. The agent's reply is step 2 and is not here yet - the
endpoint stores and acknowledges, and says nothing that implies otherwise.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from agent.providers.llm import Message as LlmMessage
from agent.reply import reply as generate_reply
from agent.tools import TakenMessage
from api.db import session_scope
from api.errors import envelope_response
from api.models import Channel, Conversation, Message
from api.notifications import raise_notification
from api.security import captcha, quota
from api.security.embed import check_origin, normalise_origin

logger = logging.getLogger("api.public_chat")

router = APIRouter(prefix="/public/chat", tags=["web chat"])

# Long enough for a real question and short enough that a paste of a novel is refused
# here rather than in the model's bill.
MESSAGE_MAX = 4000


class VisitorMessage(BaseModel):
    text: str = Field(min_length=1, max_length=MESSAGE_MAX)
    # Present after the first message, so the visitor's thread continues instead of
    # starting again. Unguessable, and checked against the channel below - it is the
    # only handle the widget has on anything.
    conversation: str | None = Field(default=None, max_length=64)
    # reCAPTCHA v3's token, when the channel has it switched on. Long: Google's are
    # already several hundred characters and the length is theirs to change.
    captcha: str | None = Field(default=None, max_length=4000)


class Accepted(BaseModel):
    """What a stranger is allowed to know: the thread, and that the message landed.

    No workspace, no channel id, no assistant name, no counts.
    """

    conversation: str
    message_id: int


class Line(BaseModel):
    """One turn of a thread, as a stranger may read it back.

    Three fields, and none of them is an id from this installation: a visitor holding
    the handle is allowed to see their own conversation, not to learn how many
    conversations exist or which workspace this is.
    """

    speaker: str
    text: str
    ts_ms: int


class Thread(BaseModel):
    """A conversation as its own visitor sees it after a reload."""

    conversation: str
    messages: list[Line]


def _too_many() -> object:
    """The one refusal that says what it is.

    Unlike the origin check, this one is safe to be honest about: the caller already
    passed the allowlist, so it is a page the business chose to let in. Telling it to
    slow down is how a widget behaves well; telling it nothing would have it retry.
    """
    return envelope_response(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        code="too_many_messages",
        message="Too many messages just now. Wait a moment and try again.",
    )


def _refused() -> object:
    """One answer for every reason a request does not belong here.

    Not found, disabled, wrong origin: the same body and the same status. The reason is
    logged, never returned - see the module docstring.
    """
    return envelope_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="origin_not_allowed",
        message="This page is not allowed to use this chat.",
    )


# What the tray shows of a message. Long enough to tell one arrival from another, short
# enough that a row stays a row.
PREVIEW_MAX = 80


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= PREVIEW_MAX:
        return collapsed
    # Cut on a word where there is one nearby, because a preview that ends mid-word
    # reads as corrupted rather than as trimmed.
    cut = collapsed[: PREVIEW_MAX - 1]
    space = cut.rfind(" ")
    if space > PREVIEW_MAX - 20:
        cut = cut[:space]
    return cut.rstrip() + "…"


async def _channel(db: DbSession, path: str) -> Channel | None:
    """The web channel this address belongs to, if it is one and it is on."""
    return await db.scalar(
        select(Channel).where(
            Channel.webhook_path == path,
            Channel.kind == "web",
            Channel.status == "active",
        )
    )


@router.post(
    "/{path}/messages",
    response_model=Accepted,
    status_code=status.HTTP_201_CREATED,
    summary="A visitor's message from an embedded widget",
)
async def post_message(
    request: Request,
    path: str,
    payload: VisitorMessage,
    origin: str | None = Header(default=None),
) -> object:
    db: DbSession = request.state.db

    channel = await _channel(db, path)
    if channel is None:
        # Logged at info, not warning: an unknown address is what a stale embed on a
        # page somebody forgot to remove looks like, and that is not an incident.
        logger.info("web chat refused", extra={"reason": "no such channel", "path": path})
        return _refused()

    settings = channel.settings_json or {}
    # `own` is this installation's origin: the widget is served from here, inside an
    # iframe on the customer's page, so the browser stamps this origin on its POST
    # rather than the site the iframe sits in. See `check_origin` for why accepting it
    # gives a browser nothing, and for what really decides who may embed.
    refusal = check_origin(
        origin,
        settings.get("allowed_origins"),
        own=str(request.base_url).rstrip("/"),
    )
    if refusal is not None:
        logger.info(
            "web chat refused",
            extra={"reason": refusal.reason, "channel_id": channel.id},
        )
        return _refused()

    # Counted after the origin check and before anything is written. A refused request
    # must not have cost the thing it was refused from doing - and counting before the
    # allowlist would let any site on the internet exhaust a business's budget without
    # ever being let in.
    #
    # The origin is normalised for the key, so `https://Shop.test:443` and
    # `https://shop.test` are one bucket rather than two halves of one.
    allowed_origin = normalise_origin(origin or "")
    if not await quota.consume(
        db, f"webchat:origin:{channel.id}:{allowed_origin}", quota.PER_ORIGIN
    ):
        logger.warning(
            "web chat rate limited",
            extra={"bucket": "origin", "channel_id": channel.id},
        )
        await db.commit()
        return _too_many()

    if payload.conversation is not None and not await quota.consume(
        db, f"webchat:conversation:{payload.conversation}", quota.PER_CONVERSATION
    ):
        logger.warning(
            "web chat rate limited",
            extra={"bucket": "conversation", "channel_id": channel.id},
        )
        await db.commit()
        return _too_many()

    # After the origin check and the ceiling, because it is the only one of the three
    # that leaves this machine. A request that fails either of the cheap local checks
    # must not also cost a round trip to Google.
    verdict = await captcha.verify(
        channel.credentials_encrypted,
        payload.captcha,
        threshold=settings.get("recaptcha_threshold"),
    )
    if not verdict.ok:
        logger.info(
            "web chat refused",
            extra={"reason": f"captcha: {verdict.reason}", "channel_id": channel.id},
        )
        await db.commit()
        # The same answer as a wrong origin. A bot that learns it was the captcha that
        # refused it is a bot that starts solving the captcha.
        return _refused()

    conversation = None
    if payload.conversation is not None:
        # Scoped to this channel, so a thread handle from one business cannot be
        # continued through another's widget.
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.external_id == payload.conversation,
                Conversation.channel_id == channel.id,
                Conversation.status == "open",
            )
        )
        if conversation is None:
            # A handle that does not resolve is treated as no handle at all. Saying
            # "no such conversation" would let somebody test handles against an
            # address, and the visitor's own recovery is simply a new thread.
            logger.info(
                "web chat thread handle did not resolve",
                extra={"channel_id": channel.id},
            )

    started = conversation is None
    if conversation is None:
        conversation = Conversation(
            workspace_id=channel.workspace_id,
            channel_id=channel.id,
            direction="inbound",
            # Generated here, not accepted from the client: it is the handle the widget
            # returns with, and a caller-chosen one could collide with another thread.
            external_id=_new_handle(),
            handling="ai",
            status="open",
        )
        db.add(conversation)
        await db.flush()

    message = Message(
        workspace_id=channel.workspace_id,
        conversation_id=conversation.id,
        ts_ms=int(dt.datetime.now(dt.UTC).timestamp() * 1000),
        speaker="caller",
        text=payload.text,
        # Null language is the honest value until something detects it. On a text
        # channel it also carries the meaning §B5 gives it: this line was typed.
        language=None,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    if started:
        # After the commit, and only for the first message of a thread. A visitor
        # typing five lines is one arrival; five rows in the tray would bury the one
        # that came from somebody else.
        #
        # `raise_notification` commits on its own and swallows its own failures, which
        # is the behaviour wanted here: a widget that stops accepting messages because
        # the tray is unhappy is worse than a message nobody was told about.
        await raise_notification(
            db,
            workspace_id=channel.workspace_id,
            category="review",
            message_key="web_chat_started",
            # Trimmed, and it is the visitor's own words - the tray shows it to
            # somebody deciding whether to open the thread, and a whole paragraph
            # there is a tray nobody scans.
            params={"preview": _preview(payload.text)},
            # A person has to act, because at step 2 nobody else can: the reply is a
            # greeting that says "somebody will read it", and a promise made to a
            # visitor is what puts this in the waiting list rather than the log.
            #
            # `needs_decision` is fixed at creation and never toggled, so it has to be
            # true of the moment it was raised. When the agent can answer (step 3) a new
            # chat becomes informational and this becomes False - and the rows raised
            # before then stay honest about the product that raised them.
            needs_decision=True,
            primary_action="open_conversation",
            action_payload={"conversation_id": conversation.id},
            conversation_id=conversation.id,
        )

    logger.info(
        "web chat message stored",
        extra={"channel_id": channel.id, "conversation_id": conversation.id},
    )
    return Accepted(conversation=conversation.external_id or "", message_id=message.id)


def _new_handle() -> str:
    """The thread's public handle.

    Random rather than the row id: the id would tell a visitor how many conversations
    the business has had, and let them try the one next door.
    """
    return secrets.token_urlsafe(24)


def _event(payload: dict[str, object]) -> str:
    """One server-sent event.

    `json.dumps` guarantees the payload is one line, which is what the format requires -
    a raw newline inside `data:` would end the event early and the rest would arrive as
    a field nobody reads.
    """
    body = json.dumps(payload, ensure_ascii=False)
    # Two newlines end an event. Built rather than written inline so no layer between
    # here and the file can eat one of them - which is exactly how this line first
    # arrived, as an f-string cut in half.
    return "data: " + body + "\n\n"


# How far back the model is told about. Ten exchanges is more than a web chat usually
# runs to, and the cost of the rest is paid twice: once in what the provider charges for
# a prompt, and once in the time to first token, which Rule 3 budgets at ~250 ms. A
# thread longer than this keeps answering - it just stops carrying its own beginning.
MAX_HISTORY_MESSAGES = 20

# What the model calls the two sides. `human` is a person who took over the thread: to
# the model that is still the business talking, and mapping it to anything else would
# have the agent reply to its own colleague.
_ROLES: dict[str, str] = {"caller": "user", "agent": "assistant", "human": "assistant"}

# A whisper is an instruction to the agent, not a line it said. Handed over as
# `assistant` it reads as something the agent already told the customer - so the model
# either repeats it or answers around it, and the colleague who wrote "tell her the
# quote still stands" gets neither. As a note it is what it is: something the agent
# knows and the visitor has not been told.
_WHISPER_PREFIX = "Note from a colleague, which the visitor has not seen: "


def _turn(row: Message) -> LlmMessage:
    if row.is_whisper:
        return LlmMessage(role="system", content=_WHISPER_PREFIX + row.text)
    return LlmMessage(role=_ROLES[row.speaker], content=row.text)


async def _history(db: DbSession, thread: Conversation, before: Message) -> list[LlmMessage]:
    """The thread so far, oldest first, without the line being answered.

    Ordered by `(ts_ms, id)` in both directions rather than by id alone: two messages
    can share a millisecond, and a thread handed to a model out of order is a thread
    where it answers the wrong question - which nothing downstream would notice.
    """
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == thread.id,
                    tuple_(Message.ts_ms, Message.id) < tuple_(before.ts_ms, before.id),
                )
                .order_by(Message.ts_ms.desc(), Message.id.desc())
                .limit(MAX_HISTORY_MESSAGES)
            )
        )
        .scalars()
        .all()
    )
    return [_turn(row) for row in reversed(rows) if row.speaker in _ROLES and row.text]


async def _reply_stream(
    request: Request,
    channel: Channel,
    conversation: Conversation,
    text: str,
    history: list[LlmMessage],
) -> AsyncIterator[str]:
    """The agent's answer, forwarded as it arrives, and stored when it is whole.

    **Stored at the end, not at each chunk.** A half-written reply in the archive would
    be indistinguishable from one the agent actually gave, and the transcript is what
    somebody reads back later to find out what a customer was told.

    **A visitor who closes the tab cancels it.** The generator's `finally` runs, nothing
    is stored, and no further tokens are produced - which is what Rule 3 means by
    `cancel()` not being optional, in the only shape that survives being retrofitted.
    """

    async def took(taken: TakenMessage) -> None:
        """A message the model took, put where a person will see it.

        Its own session, for the same reason the reply's write below has one: the
        middleware let go of the request's session when the response object was
        returned, and this runs long after that.

        Raised while the visitor is still reading the confirmation, not after the
        stream ends - somebody who closes the tab on that sentence has still had their
        message taken, and the promise the agent just made is already kept.
        """
        async with session_scope(request.app.state.sessionmaker) as db:
            await raise_notification(
                db,
                workspace_id=channel.workspace_id,
                category="review",
                message_key="message_taken",
                params={"name": taken.name, "reason": _preview(taken.reason)},
                # A person has to ring back. That is the whole point of a taken
                # message, and it is what puts this in the waiting list rather than
                # in the log with everything else that merely happened.
                needs_decision=True,
                primary_action="open_conversation",
                action_payload={"conversation_id": conversation.id},
                conversation_id=conversation.id,
            )
        logger.info(
            "web chat message taken",
            extra={"conversation_id": conversation.id, "urgent": taken.urgent},
        )

    pieces: list[str] = []
    # Rule 4: measured from the first call, not from the first complaint. Time to first
    # token is the number the phone milestone lives or dies on (~250 ms of an 800 ms
    # budget), and a text channel is where it is cheap to start watching it.
    started = time.perf_counter()
    try:
        async for chunk in generate_reply(text, history=history, on_message_taken=took):
            if await request.is_disconnected():
                logger.info(
                    "web chat reply cancelled by the visitor",
                    extra={"conversation_id": conversation.id},
                )
                return
            if not pieces:
                logger.info(
                    "web chat first token",
                    extra={
                        "conversation_id": conversation.id,
                        "ms": round((time.perf_counter() - started) * 1000),
                    },
                )
            pieces.append(chunk)
            yield _event({"delta": chunk})
    except asyncio.CancelledError:
        # The server is shutting down or the connection dropped mid-chunk. Same
        # outcome: say nothing, store nothing.
        logger.info("web chat reply cancelled", extra={"conversation_id": conversation.id})
        raise

    whole = "".join(pieces)
    if not whole:
        return

    # **Its own session, not `request.state.db`.** That one belongs to
    # `AuthenticationMiddleware`, which opens it, hands the response object back, and
    # closes it - and a streaming body only starts running *after* the response object
    # has been handed back. Writing the reply through it therefore takes a connection
    # out of the pool with nobody left to return it: one leaked connection per answer,
    # until the pool is empty and the widget stops replying. On PostgreSQL the leaked
    # connection also keeps its locks, which is how it stopped a test suite dead.
    async with session_scope(request.app.state.sessionmaker) as db:
        stored = Message(
            workspace_id=channel.workspace_id,
            conversation_id=conversation.id,
            ts_ms=int(dt.datetime.now(dt.UTC).timestamp() * 1000),
            speaker="agent",
            text=whole,
        )
        db.add(stored)
        await db.commit()
        await db.refresh(stored)
        message_id = stored.id

    yield _event({"done": True, "message_id": message_id})


# What a visitor is handed back after a reload. Long enough that a real exchange
# survives, short enough that the handle is not a way to pull an archive.
THREAD_MAX = 50

# What a stranger may see of who said what. `human` - a colleague who took the thread
# over - is folded into the agent on purpose: the visitor was talking to the business,
# and which desk answered is the business's own business.
_VISIBLE_SPEAKERS = {"caller": "visitor", "agent": "agent", "human": "agent"}


@router.get(
    "/{path}/messages",
    response_model=Thread,
    summary="The thread so far, for a visitor who reloaded the page",
)
async def read_thread(
    request: Request,
    path: str,
    conversation: str,
    origin: str | None = Header(default=None),
) -> object:
    """Milestone 0's *thread survives a page reload*, from the visitor's side.

    Without this the widget comes back empty after a refresh while the server still
    holds the thread - so the visitor asks again, and the agent answers a question it
    was already asked, referring to things the visitor can no longer see.

    **The handle is the whole of the authorisation, and that is deliberate.** It is
    unguessable, issued only to somebody who passed every check on the message that
    created it, and scoped to one channel - the same reasoning that guards the reply
    stream. What it does not do is widen: the guards below are the stream's own, in the
    same order, and what comes back carries nothing the stream does not.
    """
    db: DbSession = request.state.db

    channel = await _channel(db, path)
    if channel is None:
        return _refused()

    settings = channel.settings_json or {}
    refusal = check_origin(
        origin,
        settings.get("allowed_origins"),
        own=str(request.base_url).rstrip("/"),
        require_header=False,
    )
    if refusal is not None:
        logger.info("web chat thread refused", extra={"reason": refusal.reason})
        return _refused()

    thread = await db.scalar(
        select(Conversation).where(
            Conversation.external_id == conversation,
            Conversation.channel_id == channel.id,
            Conversation.status == "open",
        )
    )
    if thread is None:
        # A handle that has been closed, or was never on this channel, is a handle that
        # does not exist. The widget starts a new thread rather than showing an error a
        # visitor cannot act on.
        return _refused()

    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == thread.id,
                    # A whisper is a colleague coaching the agent mid-thread - "tell her
                    # the quote still stands". The customer never saw it, and the one
                    # place it could reach them is here, on a reload. The model is still
                    # given them, because being told things the visitor cannot see is
                    # what a whisper is for.
                    Message.is_whisper.is_(False),
                )
                .order_by(Message.ts_ms.desc(), Message.id.desc())
                .limit(THREAD_MAX)
            )
        )
        .scalars()
        .all()
    )
    return Thread(
        conversation=conversation,
        messages=[
            Line(speaker=_VISIBLE_SPEAKERS[row.speaker], text=row.text, ts_ms=row.ts_ms)
            for row in reversed(rows)
            if row.speaker in _VISIBLE_SPEAKERS
        ],
    )


@router.get("/{path}/stream", summary="The agent's reply, as it is produced")
async def stream_reply(
    request: Request,
    path: str,
    conversation: str,
    origin: str | None = Header(default=None),
) -> object:
    """Milestone 0 step 2, in the shape step 5 needs.

    A GET rather than the POST's response body, because the visitor's message has to be
    stored and acknowledged whether or not the reply ever starts - and because a
    response that carries both is a response nobody can cancel half of.

    The captcha is not repeated here: it was paid when the message was accepted, and
    asking Google twice for one exchange would double the cost of every conversation to
    verify a visitor who has not changed.
    """
    db: DbSession = request.state.db

    channel = await _channel(db, path)
    if channel is None:
        return _refused()

    settings = channel.settings_json or {}
    # `require_header=False`: this is a GET, and a browser sends no `Origin` on a
    # same-origin one - so `EventSource` opening the widget's own reply stream has no
    # header to offer. The thread handle below is what guards it, and it is a better
    # guard than a header: random, per conversation, scoped to this channel, and only
    # ever issued to somebody who passed every check on the message that created it.
    refusal = check_origin(
        origin,
        settings.get("allowed_origins"),
        own=str(request.base_url).rstrip("/"),
        require_header=False,
    )
    if refusal is not None:
        logger.info("web chat stream refused", extra={"reason": refusal.reason})
        return _refused()

    thread = await db.scalar(
        select(Conversation).where(
            Conversation.external_id == conversation,
            Conversation.channel_id == channel.id,
            Conversation.status == "open",
        )
    )
    if thread is None:
        return _refused()

    # The visitor's last message is what the agent is answering. Read here rather than
    # taken from the query string: a caller could otherwise ask for a reply to text the
    # conversation never contained.
    last = await db.scalar(
        select(Message)
        .where(Message.conversation_id == thread.id, Message.speaker == "caller")
        .order_by(Message.ts_ms.desc(), Message.id.desc())
        .limit(1)
    )
    if last is None:
        return _refused()

    if not await quota.consume(
        db, f"webchat:conversation:{conversation}", quota.PER_CONVERSATION
    ):
        await db.commit()
        return _too_many()
    await db.commit()

    return StreamingResponse(
        _reply_stream(request, channel, thread, last.text, await _history(db, thread, last)),
        media_type="text/event-stream",
        headers={
            # Without this a proxy buffers the whole stream and delivers it at once,
            # which is the failure Rule 3 exists to prevent, arriving from outside the
            # application.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-store",
        },
    )
