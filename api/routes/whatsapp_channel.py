"""The WhatsApp channel: Meta's webhook, and the card that configures it — §B13.

Two very different doors in one file, because they guard one channel. The webhook is
public in the web widget's sense (§B14): the address travels outside this
installation, so every response it can produce must be safe to show a stranger — an
unknown address, a switched-off channel and a bad signature all answer identically,
and the GET handshake confirms nothing to a caller who does not already hold the
verify token. The card is the usual admin surface, with two write-only secrets
instead of one.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import whatsapp as transport
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.whatsapp_channel")

router = APIRouter(tags=["whatsapp"])

# What a masked value starts with, per `api/routes/settings.py`.
_MASK_PREFIX_CHARACTERS = "•*"


def _refused() -> object:
    """One answer for every reason a webhook request does not belong here.

    Unknown address, disabled channel, wrong signature, wrong verify token: the same
    body and status. Distinguishing them would turn the address into an oracle -
    the same reasoning as §B14's for the widget.
    """
    return envelope_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="not_recognised",
        message="This address did not accept the request.",
    )


async def _channel_for(db: DbSession, path: str) -> Channel | None:
    return await db.scalar(
        select(Channel).where(
            Channel.webhook_path == path,
            Channel.kind == "whatsapp",
            Channel.status == "active",
        )
    )


@router.get("/public/whatsapp/{path}", include_in_schema=False)
async def verify_webhook(
    request: Request,
    path: str,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> object:
    """Meta's subscription handshake: echo the challenge, or say nothing useful."""
    db: DbSession = request.state.db
    channel = await _channel_for(db, path)
    expected = (channel.settings_json or {}).get("verify_token") if channel else None
    if (
        channel is None
        or mode != "subscribe"
        or not expected
        or not secrets.compare_digest(str(token or ""), str(expected))
    ):
        logger.info("whatsapp handshake refused", extra={"path_known": channel is not None})
        return _refused()
    return PlainTextResponse(challenge or "")


@router.post("/public/whatsapp/{path}", include_in_schema=False)
async def receive_webhook(request: Request, path: str) -> object:
    """One delivery from Meta: verify the signature, store, acknowledge, answer later.

    The 200 goes back as soon as the lines are stored, because Meta retries a slow
    webhook and a model reply takes as long as it takes - `schedule_reply` runs the
    answer on its own session after this request is finished.
    """
    db: DbSession = request.state.db
    channel = await _channel_for(db, path)
    credentials = transport.credentials_for(channel) if channel else None
    if channel is None or credentials is None:
        logger.info("whatsapp delivery refused", extra={"reason": "no such channel"})
        return _refused()

    raw = await request.body()
    if not transport.verify_signature(
        credentials[1], raw, request.headers.get("X-Hub-Signature-256")
    ):
        logger.info(
            "whatsapp delivery refused",
            extra={"reason": "bad signature", "channel_id": channel.id},
        )
        return _refused()

    import json

    try:
        body = json.loads(raw)
    except ValueError:
        return _refused()

    channel_id = channel.id
    needing_reply = await transport.ingest(db, channel, body if isinstance(body, dict) else {})
    for message_id in needing_reply:
        transport.schedule_reply(request.app.state.sessionmaker, channel_id, message_id)
    return {"ok": True}


# --- The settings card ----------------------------------------------------------

card = APIRouter(prefix="/api/channels/whatsapp", tags=["whatsapp"])


class WhatsAppChannelOut(BaseModel):
    enabled: bool
    phone_number_id: str | None
    # Masked, or null. Neither secret is ever read back.
    access_token_preview: str | None
    app_secret_preview: str | None
    # What Meta must be given, and what it will ask back during the handshake.
    callback_url: str
    verify_token: str
    # What the last connection test said this number is. Null until one has run.
    display_phone_number: str | None
    verified_name: str | None


def _out(request: Request, row: Channel) -> WhatsAppChannelOut:
    settings = row.settings_json or {}
    credentials = transport.credentials_for(row)
    base = str(request.base_url).rstrip("/")
    return WhatsAppChannelOut(
        enabled=row.status == "active",
        phone_number_id=settings.get("phone_number_id"),
        access_token_preview=mask(credentials[0]) if credentials else None,
        app_secret_preview=mask(credentials[1]) if credentials else None,
        callback_url=f"{base}/public/whatsapp/{row.webhook_path or ''}",
        verify_token=str(settings.get("verify_token") or ""),
        display_phone_number=settings.get("display_phone_number"),
        verified_name=settings.get("verified_name"),
    )


async def _find(db: DbSession, workspace_id: int) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == "whatsapp")
    )


async def _ensure(db: DbSession, workspace_id: int) -> Channel:
    row = await _find(db, workspace_id)
    if row is not None:
        if not row.webhook_path:
            row.webhook_path = secrets.token_urlsafe(24)
        settings = dict(row.settings_json or {})
        if not settings.get("verify_token"):
            settings["verify_token"] = secrets.token_urlsafe(18)
            row.settings_json = settings
        return row
    row = Channel(
        workspace_id=workspace_id,
        kind="whatsapp",
        name="WhatsApp",
        webhook_path=secrets.token_urlsafe(24),
        settings_json={"verify_token": secrets.token_urlsafe(18)},
        status="disabled",
    )
    db.add(row)
    await db.flush()
    return row


@card.get("", response_model=WhatsAppChannelOut, summary="The WhatsApp channel's settings")
async def read_settings(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> WhatsAppChannelOut:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    await db.commit()
    await db.refresh(row)
    return _out(request, row)


class WhatsAppChannelIn(BaseModel):
    enabled: bool | None = None
    phone_number_id: str | None = Field(default=None, max_length=64)
    # Write-only, as a pair: minted together in the same Meta application, and a
    # channel with one and not the other can speak but not listen. Null leaves the
    # stored pair alone, "" on either removes both, a mask-echo is ignored.
    access_token: str | None = Field(default=None, max_length=512)
    app_secret: str | None = Field(default=None, max_length=255)


@card.put("", response_model=WhatsAppChannelOut, summary="Configure the WhatsApp channel")
async def write_settings(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: WhatsAppChannelIn,
) -> object:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    sent = payload.model_dump(exclude_unset=True)

    if "phone_number_id" in sent:
        settings = dict(row.settings_json or {})
        settings["phone_number_id"] = sent["phone_number_id"] or None
        # A different number is a different bot until a test says otherwise.
        settings.pop("display_phone_number", None)
        settings.pop("verified_name", None)
        row.settings_json = settings

    if "access_token" in sent or "app_secret" in sent:
        token = sent.get("access_token")
        secret = sent.get("app_secret")
        if token == "" or secret == "":
            row.credentials_encrypted = None
            # Removed credentials take the channel down with them.
            row.status = "disabled"
        elif (token and token[0] in _MASK_PREFIX_CHARACTERS) or (
            secret and secret[0] in _MASK_PREFIX_CHARACTERS
        ):
            # The screen sent back what it was shown. Not an edit.
            logger.info(
                "whatsapp channel: ignored an echoed mask", extra={"channel_id": row.id}
            )
        elif token and secret:
            if not key_available():
                return envelope_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="encryption_key_missing",
                    message="This installation has no ENCRYPTION_KEY, so Meta "
                    "credentials cannot be stored. Set one and restart.",
                )
            transport.store_credentials(row, access_token=token, app_secret=secret)
        else:
            # One half of a pair that only works whole.
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="credentials_incomplete",
                message="Send the access token and the app secret together - they "
                "come from the same Meta application.",
            )

    if "enabled" in sent and sent["enabled"] is not None:
        if sent["enabled"] and not transport.is_ready(row):
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="credentials_incomplete",
                message="Save the phone number id, the access token and the app "
                "secret before switching it on.",
            )
        row.status = "active" if sent["enabled"] else "disabled"

    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "whatsapp_channel_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        # The fields, never their values: two of them are credentials.
        details={"channel_id": row.id, "fields": sorted(sent)},
    )
    return _out(request, row)


class TestResult(BaseModel):
    ok: bool
    display_phone_number: str | None
    verified_name: str | None


@card.post("/test", response_model=TestResult, summary="Prove the Meta credentials work")
async def test_connection(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> object:
    """Asks the Graph API who the configured number is — §A6.8's "Test connection",
    asked of the saved credentials rather than of ones still in the form."""
    db: DbSession = request.state.db
    row = await _find(db, context.id)
    credentials = transport.credentials_for(row) if row is not None else None
    phone_number_id = str((row.settings_json or {}).get("phone_number_id") or "") if row else ""
    if row is None or credentials is None or not phone_number_id:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="credentials_incomplete",
            message="Save the phone number id, the access token and the app secret first.",
        )

    try:
        async with transport.make_client() as client:
            answer = await transport.fetch_number(client, credentials[0], phone_number_id)
    except (transport.WhatsAppError, httpx.HTTPError) as error:
        logger.info(
            "whatsapp test failed", extra={"channel_id": row.id, "error": str(error)[:200]}
        )
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="meta_refused",
            message="Meta did not accept these credentials. Check the token, the "
            "app secret and the phone number id in your Meta application.",
        )

    row.settings_json = {
        **(row.settings_json or {}),
        "display_phone_number": answer.get("display_phone_number"),
        "verified_name": answer.get("verified_name"),
    }
    await db.commit()
    return TestResult(
        ok=True,
        display_phone_number=answer.get("display_phone_number"),
        verified_name=answer.get("verified_name"),
    )
