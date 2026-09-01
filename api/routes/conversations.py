"""The conversations screen's endpoints — the transcript archive, read.

**`viewer` reads; only `reception` and above intervene.** The role matrix on the
settings screen says what a viewer is for in its own words: "Reads calls. Changes
nothing, answers nothing." The reads here are that sentence; the writes — the whisper
and the takeover trio below — are §A6.7's interventions, and every one of them asks
for `reception`.

**The recording path never leaves the server.** `calls.recording_path` is a filesystem
path on the machine, and a screen has no use for it; what a screen needs to know is
whether audio exists, which is a boolean. Sending the path would put the layout of
somebody's disk into a browser, and into anything that later logs a response body.

Two things this deliberately does not do yet, because nothing can produce them:
starring a thread and marking one unread are drawn on the screen and have no column
behind them. They stay drawings rather than becoming buttons that do nothing.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.conversations import (
    PAGE,
    conversations_for,
    message_counts,
    position_ms,
    previews,
    search_filter,
)
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Call, Channel, Contact, Conversation, Message, User
from api.security.permissions import WorkspaceContext, require_reception, require_viewer

logger = logging.getLogger("api.conversations")

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def _utc(value: dt.datetime | None) -> str | None:
    """Always with an offset, always UTC.

    A naive timestamp read in another timezone moves a call by hours, and a transcript
    whose lines are hours out is a transcript nobody can testify from.
    """
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
    return aware.astimezone(dt.UTC).isoformat()


class ThreadOut(BaseModel):
    id: int
    channel: str
    direction: str
    status: str
    handling: str | None
    intent: str | None
    started_at: str
    ended_at: str | None
    # Whoever is on the other end, as the channel knows them. For a call this is the
    # caller's number; for a messaging channel the channel's own thread identifier.
    # Whoever is on the other end, as the channel knows them — a caller's number, a
    # chat id. **Null on `web`**, and that is a security answer rather than a missing
    # value: see `_who`.
    who: str | None
    # And as the phonebook knows them, when it does - matched on the number at read
    # time. Null is the honest answer for a caller nobody has named yet.
    who_name: str | None
    preview: str | None
    message_count: int
    is_call: bool


class ThreadPage(BaseModel):
    threads: list[ThreadOut]
    # Said rather than inferred from a short page: a client that guesses "fewer than
    # asked for means the end" guesses wrong the first time a filter removes rows.
    has_more: bool


class MessageOut(BaseModel):
    id: int
    # Milliseconds from the start of the conversation, not a clock. See the model.
    ts_ms: int
    speaker: str
    text: str
    # An operator's instruction to the agent mid-conversation. Part of the record, and
    # flagged because the customer never saw it - a screen that drew it like any other
    # line would be showing a conversation that did not happen.
    is_whisper: bool
    # Who wrote it, when a person did. `speaker` is a role; this is the name.
    author: str | None
    stt_confidence: float | None
    language: str | None


class CallOut(BaseModel):
    from_e164: str | None
    billable_seconds: int | None
    provider_cost_micros: int | None
    # Whether audio exists - never where it is. See the module docstring.
    has_recording: bool


class ThreadDetail(ThreadOut):
    summary: str | None
    messages: list[MessageOut]
    call: CallOut | None


# Channels whose `external_id` is a credential rather than an identity.
#
# On the web channel it is the visitor's resume handle: the widget sends it to continue
# the same conversation, and possession of it is the whole authorisation
# (`api/routes/public_chat.py`). Returning it as `who` put it on a screen, into any
# screenshot of that screen, and into the hands of a `viewer` — the role that may only
# read — who could then post as that visitor.
#
# A number on a `phone` thread is the opposite: it *is* how the person is identified, it
# is what the phonebook is keyed by, and it stays.
_HANDLE_IS_A_SECRET = frozenset({"web"})


def _who(row: Conversation, channel_kind: str) -> str | None:
    """The other end's identity, or nothing when the id is a credential."""
    return None if channel_kind in _HANDLE_IS_A_SECRET else row.external_id


def _thread(
    row: Conversation,
    channel_kind: str,
    preview: str | None,
    count: int,
    is_call: bool,
    who_name: str | None = None,
) -> ThreadOut:
    return ThreadOut(
        id=row.id,
        channel=channel_kind,
        direction=row.direction,
        status=row.status,
        handling=row.handling,
        intent=row.intent,
        started_at=_utc(row.started_at) or "",
        ended_at=_utc(row.ended_at),
        who=_who(row, channel_kind),
        who_name=who_name,
        preview=preview,
        message_count=count,
        is_call=is_call,
    )


async def _names_for(
    db: DbSession, workspace_id: int, whos: list[str | None]
) -> dict[str, str]:
    """The phonebook's names for these channel identities, in one query."""
    numbers = [who for who in whos if who]
    if not numbers:
        return {}
    rows = await db.execute(
        select(Contact.e164, Contact.name).where(
            Contact.workspace_id == workspace_id, Contact.e164.in_(numbers)
        )
    )
    return dict(rows.all())


@router.get("", response_model=ThreadPage, summary="The threads in this workspace")
async def list_conversations(
    request: Request,
    context: Annotated[WorkspaceContext, require_viewer],
    channel: Annotated[str | None, Query(max_length=40)] = None,
    status_filter: Annotated[
        str | None, Query(alias="status", pattern="^(open|closed)$")
    ] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=PAGE)] = PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ThreadPage:
    """Newest first, filtered by channel, status, or what was said in it.

    `q` searches the *messages*, not the summaries, and returns the conversations they
    belong to — which is what somebody looking for "the caller who mentioned a
    refund" actually means. It runs through the full-text index; see
    `api/conversations.py` for why that query is spelled twice.
    """
    db: DbSession = request.state.db

    query = conversations_for(context.id).join(Channel, Channel.id == Conversation.channel_id)
    if channel:
        query = query.where(Channel.kind == channel)
    if status_filter:
        query = query.where(Conversation.status == status_filter)
    if q and q.strip():
        # Scoped on `messages` too, not only on the parent. The subquery is the one
        # place a search could otherwise reach across workspaces, and `messages`
        # carries `workspace_id` precisely so it does not have to join to be safe.
        matching = (
            select(Message.conversation_id)
            .where(Message.workspace_id == context.id)
            .where(search_filter(db, q.strip()))
        )
        query = query.where(Conversation.id.in_(matching))

    # One more than asked for, so "is there another page" is answered by fact rather
    # than by a second count query over the same filters.
    rows = list(
        (
            await db.execute(
                query.add_columns(Channel.kind)
                .order_by(Conversation.started_at.desc(), Conversation.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    ids = [row[0].id for row in rows]
    preview_by_id = await previews(db, ids)
    count_by_id = await message_counts(db, ids)
    call_ids = set(
        (await db.execute(select(Call.conversation_id).where(Call.conversation_id.in_(ids))))
        .scalars()
        .all()
    )
    name_by_who = await _names_for(db, context.id, [row[0].external_id for row in rows])

    return ThreadPage(
        threads=[
            _thread(
                row[0],
                row[1],
                preview_by_id.get(row[0].id),
                count_by_id.get(row[0].id, 0),
                row[0].id in call_ids,
                name_by_who.get(row[0].external_id or ""),
            )
            for row in rows
        ],
        has_more=has_more,
    )


# Registered before `/{conversation_id}`, and that order is load-bearing: FastAPI
# matches in declaration order, so with the parameterised route first this path is
# read as a conversation whose id is "meta" and answered with a validation error.
class ChannelOut(BaseModel):
    id: int
    kind: str
    name: str | None
    thread_count: int


@router.get(
    "/meta/channels",
    response_model=list[ChannelOut],
    summary="The channels this workspace has conversations on",
)
async def list_channels(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> list[Any]:
    """What the filter chips should offer.

    Built from what the workspace actually has rather than from the ten channels the
    product commits to: a chip for a channel with nothing behind it is a chip that
    always returns an empty list, and the screen already has an empty state for the
    real thing.
    """
    from sqlalchemy import func

    db: DbSession = request.state.db
    rows = await db.execute(
        select(Channel.id, Channel.kind, Channel.name, func.count(Conversation.id))
        .join(Conversation, Conversation.channel_id == Channel.id)
        .where(Conversation.workspace_id == context.id)
        .group_by(Channel.id, Channel.kind, Channel.name)
        .order_by(func.count(Conversation.id).desc())
    )
    return [
        ChannelOut(id=cid, kind=kind, name=name, thread_count=count)
        for cid, kind, name, count in rows
    ]


@router.get(
    "/{conversation_id}",
    response_model=ThreadDetail,
    summary="One thread, with what was said in it",
)
async def read_conversation(
    request: Request,
    context: Annotated[WorkspaceContext, require_viewer],
    conversation_id: int,
) -> ThreadDetail:
    db: DbSession = request.state.db

    found = (
        await db.execute(
            conversations_for(context.id)
            .join(Channel, Channel.id == Conversation.channel_id)
            .add_columns(Channel.kind)
            .where(Conversation.id == conversation_id)
        )
    ).first()
    if found is None:
        # The workspace filter is part of the lookup above, so an id belonging to
        # another workspace is indistinguishable from one that does not exist. A 403
        # here would confirm the row is real, which is the answer being withheld.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such conversation.")

    row, channel_kind = found

    messages = list(
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == row.id)
                # By position in the conversation, never by insertion order: a line
                # corrected and re-stored later must still read where it was said.
                .order_by(Message.ts_ms, Message.id)
            )
        )
        .scalars()
        .all()
    )
    call = await db.get(Call, row.id)
    name_by_who = await _names_for(db, context.id, [row.external_id])
    # One query for every author on the thread rather than one per line. Whispers are a
    # handful of lines out of hundreds, so the set is nearly always empty or tiny.
    author_ids = {m.author_user_id for m in messages if m.author_user_id is not None}
    author_by_id: dict[int, str] = (
        {
            user_id: username
            for user_id, username in (
                await db.execute(select(User.id, User.username).where(User.id.in_(author_ids)))
            ).all()
        }
        if author_ids
        else {}
    )

    return ThreadDetail(
        **_thread(
            row,
            channel_kind,
            messages[-1].text[:160] if messages else None,
            len(messages),
            call is not None,
            name_by_who.get(row.external_id or ""),
        ).model_dump(),
        summary=row.summary,
        messages=[
            MessageOut(
                id=m.id,
                ts_ms=m.ts_ms,
                speaker=m.speaker,
                text=m.text,
                is_whisper=m.is_whisper,
                author=author_by_id.get(m.author_user_id or 0),
                stt_confidence=m.stt_confidence,
                language=m.language,
            )
            for m in messages
        ],
        call=(
            CallOut(
                from_e164=call.from_e164,
                billable_seconds=call.billable_seconds,
                provider_cost_micros=call.provider_cost_micros,
                has_recording=bool(call.recording_path),
            )
            if call is not None
            else None
        ),
    )


class Whisper(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post(
    "/{conversation_id}/whisper",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Say something to the agent that the customer will not see",
)
async def whisper(
    request: Request,
    context: Annotated[WorkspaceContext, require_reception],
    user: CurrentUser,
    conversation_id: int,
    payload: Whisper,
) -> object:
    """§A6.7's first intervention, and the one it calls highest value and lowest cost.

    The agent is told; the customer is not. Both halves of that already existed - the
    model is handed a whisper as a system note and the widget's thread filters it out
    (`api/routes/public_chat.py`) - and there was no way to write one. This is it.

    **Reception, not viewer.** Reading a transcript and putting words into the agent's
    mouth are different powers: this line becomes part of what a customer is told.

    **A closed thread is refused rather than accepted quietly.** Nothing is listening to
    it, so a whisper written into one is coaching for nobody - and it would sit in the
    archive looking exactly like coaching that arrived in time.
    """
    db: DbSession = request.state.db
    text = payload.text.strip()
    if not text:
        return envelope_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="empty_whisper",
            message="A whisper needs something in it.",
        )

    row = await db.scalar(
        conversations_for(context.id).where(Conversation.id == conversation_id)
    )
    if row is None:
        # Same silence as the read above: another workspace's id answers like one that
        # does not exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such conversation.")
    if row.status != "open":
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="conversation_closed",
            message="This conversation has ended, so nothing is listening to a whisper.",
        )

    line = Message(
        workspace_id=context.id,
        conversation_id=row.id,
        # A position in the conversation, like every other line, so the whisper sorts
        # among the ones it belongs between rather than at one end of them.
        ts_ms=position_ms(row.started_at),
        speaker="human",
        text=text,
        is_whisper=True,
        author_user_id=user.id,
        # Null on a text channel, and the null is §B5's own signal that this was typed.
        stt_confidence=None,
        language=None,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)

    logger.info("whisper written into conversation %s by %s", row.id, user.username)
    return MessageOut(
        id=line.id,
        ts_ms=line.ts_ms,
        speaker=line.speaker,
        text=line.text,
        is_whisper=line.is_whisper,
        author=user.username,
        stt_confidence=line.stt_confidence,
        language=line.language,
    )


class HandlingOut(BaseModel):
    """What the two switches answer with: the thread, and whose it is now."""

    id: int
    handling: str


async def _open_thread(
    db: DbSession, workspace_id: int, conversation_id: int
) -> Conversation | object:
    """The thread an intervention may act on, or the refusal it gets instead.

    The same two walls as the whisper's, in the same order: another workspace's id
    answers like one that does not exist, and a thread that has ended refuses rather
    than accepting quietly — nobody is on the other end of it to be spoken to.
    """
    row = await db.scalar(
        conversations_for(workspace_id).where(Conversation.id == conversation_id)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No such conversation.")
    if row.status != "open":
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="conversation_closed",
            message="This conversation has ended.",
        )
    return row


@router.post(
    "/{conversation_id}/takeover",
    response_model=HandlingOut,
    summary="Take the conversation over from the agent",
)
async def take_over(
    request: Request,
    context: Annotated[WorkspaceContext, require_reception],
    user: CurrentUser,
    conversation_id: int,
) -> object:
    """§A6.7's second intervention: the agent goes silent and a person answers.

    From this moment the widget's reply stream produces nothing and stores nothing
    (`api/routes/public_chat.py`), and the `reply` endpoint below is what speaks. An
    agent that kept talking would be two voices answering one customer.

    **Pressing it twice is one takeover, not a conflict.** The live screen polls, so
    two colleagues can press within a refresh of each other — the second press changes
    nothing, and refusing it would surface as an error to somebody who merely lost
    the race.
    """
    db: DbSession = request.state.db
    row = await _open_thread(db, context.id, conversation_id)
    if not isinstance(row, Conversation):
        return row

    row.handling = "human"
    await db.commit()
    logger.info("conversation %s taken over by %s", row.id, user.username)
    return HandlingOut(id=row.id, handling="human")


@router.post(
    "/{conversation_id}/resume",
    response_model=HandlingOut,
    summary="Hand the conversation back to the agent",
)
async def resume(
    request: Request,
    context: Annotated[WorkspaceContext, require_reception],
    user: CurrentUser,
    conversation_id: int,
) -> object:
    """The takeover's other half: the agent answers the next message as before.

    Idempotent for the same reason taking over is — a thread already back with the
    agent stays there, and the answer says so.
    """
    db: DbSession = request.state.db
    row = await _open_thread(db, context.id, conversation_id)
    if not isinstance(row, Conversation):
        return row

    row.handling = "ai"
    await db.commit()
    logger.info("conversation %s handed back to the agent by %s", row.id, user.username)
    return HandlingOut(id=row.id, handling="ai")


class HumanReply(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post(
    "/{conversation_id}/reply",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Answer the customer yourself, as the business",
)
async def reply_as_business(
    request: Request,
    context: Annotated[WorkspaceContext, require_reception],
    user: CurrentUser,
    conversation_id: int,
    payload: HumanReply,
) -> object:
    """A person typing as the agent — what §A6.7 calls *types and the agent voices it*.

    On a text channel there is no voicing: the line goes to the customer as the
    business, and the widget's thread shows it exactly as it shows the agent
    (`_VISIBLE_SPEAKERS` folds `human` into `agent` on purpose — which desk answered
    is the business's own business).

    **Only while the thread is taken over.** Outside that mode the box a person types
    into is the whisper: a reply landing beside the agent's own answer would be the
    two-voices problem the `handling` flag exists to prevent.
    """
    db: DbSession = request.state.db
    text = payload.text.strip()
    if not text:
        return envelope_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="empty_reply",
            message="A reply needs something in it.",
        )

    row = await _open_thread(db, context.id, conversation_id)
    if not isinstance(row, Conversation):
        return row
    if row.handling != "human":
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="not_taken_over",
            message="Take the conversation over first — the agent is still answering it.",
        )

    # On a channel with a push transport the reply is delivered *before* it is
    # stored. The transcript is what somebody reads back to learn what a customer was
    # told, and a line that failed to reach them must not sit in it looking like one
    # that did — the web channel is the other way round only because its widget polls
    # the store, so there storing *is* delivering.
    channel = await db.scalar(select(Channel).where(Channel.id == row.channel_id))
    if channel is not None and channel.kind == "telegram":
        import httpx

        from api.channels import telegram

        if not channel.credentials_encrypted or not row.external_id:
            return envelope_response(
                status_code=status.HTTP_409_CONFLICT,
                code="no_bot_token",
                message="This conversation's Telegram channel has no bot token saved.",
            )
        try:
            async with telegram.make_client() as client:
                await telegram.send_text(
                    client, channel.credentials_encrypted, row.external_id, text
                )
        except (telegram.TelegramError, httpx.HTTPError) as error:
            logger.warning(
                "telegram reply not delivered",
                extra={"conversation_id": row.id, "error": str(error)},
            )
            return envelope_response(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="not_delivered",
                message="Telegram did not accept the message, so nothing was written. "
                "Check the channel's bot token and try again.",
            )

    if channel is not None and channel.kind == "email":
        from api.channels import email as email_transport

        config = email_transport.config_for(channel)
        if config is None or not row.external_id:
            return envelope_response(
                status_code=status.HTTP_409_CONFLICT,
                code="mailbox_incomplete",
                message="This conversation's email channel has no complete mailbox saved.",
            )
        state = email_transport.thread_state(row)
        try:
            await email_transport.send_mail(
                config,
                to=row.external_id,
                subject=email_transport.reply_subject(str(state.get("subject") or "")),
                text=text,
                in_reply_to=state.get("last_message_id") or None,
            )
        except email_transport.EmailError as error:
            logger.warning(
                "email reply not delivered",
                extra={"conversation_id": row.id, "error": str(error)[:200]},
            )
            return envelope_response(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="not_delivered",
                message="The mailbox did not accept the message, so nothing was "
                "written. Check the channel's mailbox settings and try again.",
            )

    line = Message(
        workspace_id=context.id,
        conversation_id=row.id,
        ts_ms=position_ms(row.started_at),
        speaker="human",
        text=text,
        is_whisper=False,
        author_user_id=user.id,
        # Null on a text channel, and the null is §B5's own signal that this was typed.
        stt_confidence=None,
        language=None,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)

    logger.info("human reply written into conversation %s by %s", row.id, user.username)
    return MessageOut(
        id=line.id,
        ts_ms=line.ts_ms,
        speaker=line.speaker,
        text=line.text,
        is_whisper=line.is_whisper,
        author=user.username,
        stt_confidence=line.stt_confidence,
        language=line.language,
    )
