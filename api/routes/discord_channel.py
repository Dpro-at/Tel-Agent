"""The Discord channel's settings — the Telegram card's contract, §A6.8.

One secret, because Discord mints one: the bot token from the customer's own
developer portal. What the card cannot do for the operator it says plainly - the
MESSAGE CONTENT intent lives in the portal and nothing here can switch it on, so
the copy points at it; a bot without it hears every message as empty.
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import discord as transport
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.discord_channel")

router = APIRouter(prefix="/api/channels/discord", tags=["discord"])

_MASK_PREFIX_CHARACTERS = "•*"


class DiscordChannelOut(BaseModel):
    enabled: bool
    bot_token_preview: str | None
    # What the last connection test said this bot is called. Null until one has run.
    bot_username: str | None


def _out(row: Channel) -> DiscordChannelOut:
    return DiscordChannelOut(
        enabled=row.status == "active",
        bot_token_preview=mask(row.credentials_encrypted)
        if row.credentials_encrypted
        else None,
        bot_username=(row.settings_json or {}).get("bot_username"),
    )


async def _find(db: DbSession, workspace_id: int) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == "discord")
    )


async def _ensure(db: DbSession, workspace_id: int) -> Channel:
    row = await _find(db, workspace_id)
    if row is not None:
        return row
    row = Channel(
        workspace_id=workspace_id,
        kind="discord",
        name="Discord",
        settings_json={},
        status="disabled",
    )
    db.add(row)
    await db.flush()
    return row


@router.get("", response_model=DiscordChannelOut, summary="The Discord channel's settings")
async def read_settings(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> DiscordChannelOut:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    await db.commit()
    await db.refresh(row)
    return _out(row)


class DiscordChannelIn(BaseModel):
    enabled: bool | None = None
    # Write-only: null keeps, "" removes (and switches off), a mask-echo is ignored.
    bot_token: str | None = Field(default=None, max_length=255)


@router.put("", response_model=DiscordChannelOut, summary="Configure the Discord channel")
async def write_settings(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: DiscordChannelIn,
) -> object:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    sent = payload.model_dump(exclude_unset=True)

    if "bot_token" in sent:
        token = sent["bot_token"]
        if token == "":
            row.credentials_encrypted = None
            row.status = "disabled"
            row.settings_json = {
                key: value
                for key, value in (row.settings_json or {}).items()
                if key != "bot_username"
            }
        elif token and token[0] in _MASK_PREFIX_CHARACTERS:
            logger.info("discord channel: ignored an echoed mask", extra={"channel_id": row.id})
        elif token:
            if not key_available():
                return envelope_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="encryption_key_missing",
                    message="This installation has no ENCRYPTION_KEY, so a bot token "
                    "cannot be stored. Set one and restart.",
                )
            row.credentials_encrypted = token
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
                message="Save the bot token from the Discord developer portal before "
                "switching it on.",
            )
        row.status = "active" if sent["enabled"] else "disabled"

    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "discord_channel_changed",
        request=request,
        user_id=user.id,
        username=user.username,
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
    db: DbSession = request.state.db
    row = await _find(db, context.id)
    if row is None or not row.credentials_encrypted:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="no_bot_token",
            message="Save a bot token first.",
        )

    try:
        async with transport.make_client() as client:
            me = await transport.fetch_me(client, row.credentials_encrypted)
    except (transport.DiscordError, httpx.HTTPError) as error:
        logger.info(
            "discord test failed", extra={"channel_id": row.id, "error": str(error)[:200]}
        )
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="discord_refused",
            message="Discord did not accept this token. Check it in the developer portal.",
        )

    username = me.get("username") if isinstance(me, dict) else None
    row.settings_json = {**(row.settings_json or {}), "bot_username": username}
    await db.commit()
    return TestResult(ok=True, bot_username=username)
