"""The Telegram channel's settings — §A6.8's Channels tab, second card.

The shape is the web channel's, because the questions are the same: one channel per
workspace created on first read, a credential masked on read with the mask-echo
ignored on write, and a switch that refuses to turn on while the thing it switches
cannot work. What differs is only what §B13 says differs — the credential is a bot
token from the customer's own @BotFather, and the "Test connection" §A6.8 requires
asks Telegram `getMe`, proving the link rather than claiming it.

**One Telegram channel per workspace, for now.** §B9.2 allows for two bots one day;
this card, like the web chat's, starts with the case every installation has. Adding a
second is a decision to reopen this, not a POST.
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import telegram
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.telegram_channel")

router = APIRouter(prefix="/api/channels/telegram", tags=["telegram"])

# What a masked value starts with, per `api/routes/settings.py`.
_MASK_PREFIX_CHARACTERS = "•*"


class TelegramChannelOut(BaseModel):
    enabled: bool
    # Masked, or null when none is stored. Never the token.
    bot_token_preview: str | None
    # What `getMe` last said this token is, cached from the test so the card can name
    # the bot without a network call on every read. Null until a test has run.
    bot_username: str | None


def _out(row: Channel) -> TelegramChannelOut:
    settings = row.settings_json or {}
    return TelegramChannelOut(
        enabled=row.status == "active",
        bot_token_preview=mask(row.credentials_encrypted)
        if row.credentials_encrypted
        else None,
        bot_username=settings.get("bot_username"),
    )


async def _find(db: DbSession, workspace_id: int) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == "telegram")
    )


async def _ensure(db: DbSession, workspace_id: int) -> Channel:
    row = await _find(db, workspace_id)
    if row is not None:
        return row
    row = Channel(
        workspace_id=workspace_id,
        kind="telegram",
        name="Telegram",
        settings_json={},
        # Off until a token is saved and somebody switches it on - the same reasoning
        # as the web channel's empty allowlist.
        status="disabled",
    )
    db.add(row)
    await db.flush()
    return row


@router.get("", response_model=TelegramChannelOut, summary="The Telegram channel's settings")
async def read_settings(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> TelegramChannelOut:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    await db.commit()
    await db.refresh(row)
    return _out(row)


class TelegramChannelIn(BaseModel):
    enabled: bool | None = None
    # Write-only. Sent as null to leave it alone, as "" to remove it, and never read
    # back - the response carries a mask instead.
    bot_token: str | None = Field(default=None, max_length=255)


@router.put("", response_model=TelegramChannelOut, summary="Configure the Telegram channel")
async def write_settings(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: TelegramChannelIn,
) -> object:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    sent = payload.model_dump(exclude_unset=True)

    if "bot_token" in sent:
        token = sent["bot_token"]
        if token == "":
            row.credentials_encrypted = None
            # A removed token takes the channel down with it: polling with nothing
            # would only log failures nobody can act on.
            row.status = "disabled"
            row.settings_json = {
                key: value
                for key, value in (row.settings_json or {}).items()
                if key != "bot_username"
            }
        elif token and token[0] in _MASK_PREFIX_CHARACTERS:
            # The screen sent back what it was shown. Not an edit.
            logger.info(
                "telegram channel: ignored an echoed mask", extra={"channel_id": row.id}
            )
        elif token:
            if not key_available():
                return envelope_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="encryption_key_missing",
                    message="This installation has no ENCRYPTION_KEY, so a bot token "
                    "cannot be stored. Set one and restart.",
                )
            row.credentials_encrypted = token
            # A new token is a new bot until a test says otherwise.
            row.settings_json = {
                key: value
                for key, value in (row.settings_json or {}).items()
                if key != "bot_username"
            }

    if "enabled" in sent and sent["enabled"] is not None:
        if sent["enabled"] and not row.credentials_encrypted:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="no_bot_token",
                message="Save the bot token from @BotFather before switching it on.",
            )
        row.status = "active" if sent["enabled"] else "disabled"

    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "telegram_channel_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        # The fields, never their values: one of them is a credential.
        details={"channel_id": row.id, "fields": sorted(sent)},
    )
    return _out(row)


class TestResult(BaseModel):
    ok: bool
    bot_username: str | None


@router.post("/test", response_model=TestResult, summary="Prove the bot token works")
async def test_connection(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> object:
    """`getMe` with the stored token — §A6.8's "Test connection", asked of the saved
    credential rather than of one still in the form, so what is proven is what will
    actually poll."""
    db: DbSession = request.state.db
    row = await _find(db, context.id)
    if row is None or not row.credentials_encrypted:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="no_bot_token",
            message="Save a bot token first.",
        )

    try:
        async with telegram.make_client() as client:
            me = await telegram.get_me(client, row.credentials_encrypted)
    except (telegram.TelegramError, httpx.HTTPError) as error:
        logger.info("telegram test failed", extra={"channel_id": row.id, "error": str(error)})
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="telegram_refused",
            message="Telegram did not accept this token. Check it against @BotFather.",
        )

    username = me.get("username") if isinstance(me, dict) else None
    row.settings_json = {**(row.settings_json or {}), "bot_username": username}
    await db.commit()
    return TestResult(ok=True, bot_username=username)
