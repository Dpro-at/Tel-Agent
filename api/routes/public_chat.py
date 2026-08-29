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

import datetime as dt
import logging
import secrets

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.errors import envelope_response
from api.models import Channel, Conversation, Message
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
