"""The email channel's settings — §A6.8's Channels tab, third card.

The contract is the other two cards': one channel per workspace created on first
read, the password masked on read and the mask-echo ignored on write, a switch that
refuses to turn on while the mailbox is incomplete, and a "Test connection" that
proves the link rather than claiming it — both links here, because a mailbox that
receives but cannot send is an agent that reads customers' mail and never answers.

**This mailbox is not the installation's notification SMTP.** The settings store's
`smtp.*` keys are how Tel-Agent talks to its operator; this card is how a business
talks to its customers, per workspace, on credentials the customer owns (§B9.2).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import email as email_transport
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.email_channel")

router = APIRouter(prefix="/api/channels/email", tags=["email"])

# What a masked value starts with, per `api/routes/settings.py`.
_MASK_PREFIX_CHARACTERS = "•*"

# The settings_json keys the card owns. Everything non-secret lives there; the
# password alone is the credential and lives in the encrypted column.
_FIELDS = (
    "imap_host",
    "imap_port",
    "smtp_host",
    "smtp_port",
    "username",
    "from_address",
    "imap_ssl",
    "smtp_tls",
    "smtp_ssl",
)


class EmailChannelOut(BaseModel):
    enabled: bool
    imap_host: str | None
    imap_port: int
    smtp_host: str | None
    smtp_port: int
    username: str | None
    from_address: str | None
    imap_ssl: bool
    smtp_tls: bool
    smtp_ssl: bool
    # Masked, or null when none is stored. Never the password.
    password_preview: str | None


def _out(row: Channel) -> EmailChannelOut:
    settings = row.settings_json or {}
    return EmailChannelOut(
        enabled=row.status == "active",
        imap_host=settings.get("imap_host"),
        imap_port=int(settings.get("imap_port") or 993),
        smtp_host=settings.get("smtp_host"),
        smtp_port=int(settings.get("smtp_port") or 587),
        username=settings.get("username"),
        from_address=settings.get("from_address"),
        imap_ssl=bool(settings.get("imap_ssl", True)),
        smtp_tls=bool(settings.get("smtp_tls", True)),
        smtp_ssl=bool(settings.get("smtp_ssl", False)),
        password_preview=mask(row.credentials_encrypted) if row.credentials_encrypted else None,
    )


async def _find(db: DbSession, workspace_id: int) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == "email")
    )


async def _ensure(db: DbSession, workspace_id: int) -> Channel:
    row = await _find(db, workspace_id)
    if row is not None:
        return row
    row = Channel(
        workspace_id=workspace_id,
        kind="email",
        name="Email",
        settings_json={},
        # Off until the mailbox is complete and somebody switches it on.
        status="disabled",
    )
    db.add(row)
    await db.flush()
    return row


@router.get("", response_model=EmailChannelOut, summary="The email channel's settings")
async def read_settings(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> EmailChannelOut:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    await db.commit()
    await db.refresh(row)
    return _out(row)


class EmailChannelIn(BaseModel):
    enabled: bool | None = None
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    from_address: str | None = Field(default=None, max_length=255)
    imap_ssl: bool | None = None
    smtp_tls: bool | None = None
    smtp_ssl: bool | None = None
    # Write-only. Sent as null to leave it alone, as "" to remove it, and never read
    # back - the response carries a mask instead.
    password: str | None = Field(default=None, max_length=255)


@router.put("", response_model=EmailChannelOut, summary="Configure the email channel")
async def write_settings(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: EmailChannelIn,
) -> object:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    sent = payload.model_dump(exclude_unset=True)

    settings = dict(row.settings_json or {})
    for field in _FIELDS:
        if field in sent:
            settings[field] = sent[field]
    row.settings_json = settings

    if "password" in sent:
        password = sent["password"]
        if password == "":
            row.credentials_encrypted = None
            # A removed password takes the channel down with it: polling with
            # nothing would only log failures nobody can act on.
            row.status = "disabled"
        elif password and password[0] in _MASK_PREFIX_CHARACTERS:
            # The screen sent back what it was shown. Not an edit.
            logger.info("email channel: ignored an echoed mask", extra={"channel_id": row.id})
        elif password:
            if not key_available():
                return envelope_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="encryption_key_missing",
                    message="This installation has no ENCRYPTION_KEY, so a mailbox "
                    "password cannot be stored. Set one and restart.",
                )
            row.credentials_encrypted = password

    if "enabled" in sent and sent["enabled"] is not None:
        if sent["enabled"] and email_transport.config_for(row) is None:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="mailbox_incomplete",
                message="Fill in the IMAP host, the SMTP host, the username and the "
                "password before switching it on.",
            )
        row.status = "active" if sent["enabled"] else "disabled"

    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "email_channel_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        # The fields, never their values: one of them is a credential and the rest
        # name the customer's mail infrastructure.
        details={"channel_id": row.id, "fields": sorted(sent)},
    )
    return _out(row)


class TestResult(BaseModel):
    ok: bool


@router.post("/test", response_model=TestResult, summary="Prove the mailbox works")
async def test_connection(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id)
    config = email_transport.config_for(row) if row is not None else None
    if config is None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="mailbox_incomplete",
            message="Fill in the mailbox first.",
        )

    try:
        await asyncio.to_thread(email_transport.check_blocking, config)
    except email_transport.EmailError as error:
        logger.info(
            "email test failed", extra={"channel_id": row.id, "error": str(error)[:200]}
        )
        # Which half refused is the one thing the person can act on, and it names
        # the customer's own server rather than anything of ours.
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="mailbox_refused",
            message=f"The mailbox did not accept the connection. {error}",
        )
    return TestResult(ok=True)
