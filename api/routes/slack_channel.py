"""The Slack channel's settings — the Meta cards' pair contract, §A6.8.

Two tokens minted together in the customer's own Slack app: the app-level token
(`xapp-…`) that opens the socket and the bot token (`xoxb-…`) that speaks. A
channel with one and not the other can listen but not answer, or answer but not
listen, so they travel as a pair. Socket Mode means no callback URL to hand out —
the card is credentials and a test, nothing else.
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import slack as transport
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.slack_channel")

router = APIRouter(prefix="/api/channels/slack", tags=["slack"])

_MASK_PREFIX_CHARACTERS = "•*"


class SlackChannelOut(BaseModel):
    enabled: bool
    app_token_preview: str | None
    bot_token_preview: str | None
    # What the last connection test said: the workspace and the bot's user name.
    team_name: str | None
    bot_name: str | None


def _out(row: Channel) -> SlackChannelOut:
    settings = row.settings_json or {}
    credentials = transport.credentials_for(row)
    return SlackChannelOut(
        enabled=row.status == "active",
        app_token_preview=mask(credentials[0]) if credentials else None,
        bot_token_preview=mask(credentials[1]) if credentials else None,
        team_name=settings.get("team_name"),
        bot_name=settings.get("bot_name"),
    )


async def _find(db: DbSession, workspace_id: int) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == "slack")
    )


async def _ensure(db: DbSession, workspace_id: int) -> Channel:
    row = await _find(db, workspace_id)
    if row is not None:
        return row
    row = Channel(
        workspace_id=workspace_id,
        kind="slack",
        name="Slack",
        settings_json={},
        status="disabled",
    )
    db.add(row)
    await db.flush()
    return row


@router.get("", response_model=SlackChannelOut, summary="The Slack channel's settings")
async def read_settings(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> SlackChannelOut:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    await db.commit()
    await db.refresh(row)
    return _out(row)


class SlackChannelIn(BaseModel):
    enabled: bool | None = None
    # Write-only, as a pair. Null leaves the stored pair alone, "" on either removes
    # both, a mask-echo is ignored.
    app_token: str | None = Field(default=None, max_length=512)
    bot_token: str | None = Field(default=None, max_length=512)


@router.put("", response_model=SlackChannelOut, summary="Configure the Slack channel")
async def write_settings(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: SlackChannelIn,
) -> object:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    sent = payload.model_dump(exclude_unset=True)

    if "app_token" in sent or "bot_token" in sent:
        app_token = sent.get("app_token")
        bot_token = sent.get("bot_token")
        if app_token == "" or bot_token == "":
            row.credentials_encrypted = None
            row.status = "disabled"
            row.settings_json = {
                key: value
                for key, value in (row.settings_json or {}).items()
                if key not in ("team_name", "bot_name")
            }
        elif (app_token and app_token[0] in _MASK_PREFIX_CHARACTERS) or (
            bot_token and bot_token[0] in _MASK_PREFIX_CHARACTERS
        ):
            logger.info("slack channel: ignored an echoed mask", extra={"channel_id": row.id})
        elif app_token and bot_token:
            if not key_available():
                return envelope_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="encryption_key_missing",
                    message="This installation has no ENCRYPTION_KEY, so Slack "
                    "tokens cannot be stored. Set one and restart.",
                )
            transport.store_credentials(row, app_token=app_token, bot_token=bot_token)
            row.settings_json = {
                key: value
                for key, value in (row.settings_json or {}).items()
                if key not in ("team_name", "bot_name")
            }
        else:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="credentials_incomplete",
                message="Send the app-level token and the bot token together - they "
                "come from the same Slack app.",
            )

    if "enabled" in sent and sent["enabled"] is not None:
        if sent["enabled"] and transport.credentials_for(row) is None:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="credentials_incomplete",
                message="Save the app-level token and the bot token before switching it on.",
            )
        row.status = "active" if sent["enabled"] else "disabled"

    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "slack_channel_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        # The fields, never their values: two of them are credentials.
        details={"channel_id": row.id, "fields": sorted(sent)},
    )
    return _out(row)


class TestResult(BaseModel):
    ok: bool
    team_name: str | None
    bot_name: str | None


@router.post("/test", response_model=TestResult, summary="Prove the Slack tokens work")
async def test_connection(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> object:
    """`auth.test` with the bot token, and a socket address with the app token -
    both halves proven, because a pair that half-works reads as a working channel
    that never answers."""
    db: DbSession = request.state.db
    row = await _find(db, context.id)
    credentials = transport.credentials_for(row) if row is not None else None
    if row is None or credentials is None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="credentials_incomplete",
            message="Save both tokens first.",
        )

    try:
        async with transport.make_client() as client:
            me = await transport.auth_test(client, credentials[1])
            await transport.socket_url(client, credentials[0])
    except (transport.SlackError, httpx.HTTPError) as error:
        logger.info(
            "slack test failed", extra={"channel_id": row.id, "error": str(error)[:200]}
        )
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="slack_refused",
            message="Slack did not accept these tokens. Check both in your Slack app.",
        )

    team = me.get("team")
    bot = me.get("user")
    row.settings_json = {**(row.settings_json or {}), "team_name": team, "bot_name": bot}
    await db.commit()
    return TestResult(ok=True, team_name=team, bot_name=bot)
