"""The web chat channel's settings — §A6.8's Channels tab, for the one channel that exists.

Until this, the guards in §B14 could only be configured with SQL. A guard nobody can
switch on is a guard that is off, so this is not decoration on the endpoint that landed
before it - it is the half that makes it usable.

**One web channel per workspace.** §B13 gives each channel its own card and its own
credentials; a second web chat on the same workspace would mean two addresses answering
for one business with two allowlists to keep in step. Adding a second is a decision to
reopen this, not a POST.

**The address is generated here and never chosen.** It is `channels.webhook_path`, the
same column the widget's endpoint looks up, and the same shape §B14 describes: long,
random, unique. A chosen one would be guessable, and the allowlist is what protects it
either way - but a guessable address invites the attempt.

**The secret is masked on read and mask-echo is ignored on write**, exactly as the
settings store does it. A screen that renders `••••3ab1` and saves the form must not
save the bullets over the live credential.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.captcha import DEFAULT_THRESHOLD
from api.security.crypto import key_available, mask
from api.security.embed import normalise_origin
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.web_channel")

router = APIRouter(prefix="/api/channels/web", tags=["web chat"])

# What a masked value starts with, per `api/routes/settings.py`. Asked as "does it
# begin with bullets" rather than "is it all bullets": the mask keeps the last four
# characters, so the second question never matches and the mask gets saved.
_MASK_PREFIX_CHARACTERS = "•*"

# Enough origins for a business with a few domains, and few enough that the list stays
# something a person reads rather than a list they manage.
MAX_ORIGINS = 20


class WebChannelOut(BaseModel):
    enabled: bool
    # The origins allowed to embed the widget. Empty means the channel refuses
    # everything - see §B14; it is not the same as "not restricted".
    allowed_origins: list[str]
    recaptcha_site_key: str | None
    recaptcha_threshold: float
    # Masked, or null when none is stored. Never the secret.
    recaptcha_secret_preview: str | None
    # The address in the embed tag. Public by design: it travels in the customer's HTML.
    embed_path: str
    embed_snippet: str


def _snippet(request: Request, path: str) -> str:
    """The line the customer pastes, built against the address they will actually use.

    Composed from the request's own base URL rather than a configured one: an
    installation reached at `telagent.wagner-partner.local` must be told to paste that,
    and a hard-coded hostname is how a snippet ends up pointing at the developer's
    machine.
    """
    base = str(request.base_url).rstrip("/")
    return f'<script src="{base}/embed.js" data-tel-agent="{path}" defer></script>'


def _out(request: Request, row: Channel) -> WebChannelOut:
    settings = row.settings_json or {}
    return WebChannelOut(
        enabled=row.status == "active",
        allowed_origins=list(settings.get("allowed_origins") or []),
        recaptcha_site_key=settings.get("recaptcha_site_key"),
        recaptcha_threshold=float(settings.get("recaptcha_threshold") or DEFAULT_THRESHOLD),
        recaptcha_secret_preview=mask(row.credentials_encrypted)
        if row.credentials_encrypted
        else None,
        embed_path=row.webhook_path or "",
        embed_snippet=_snippet(request, row.webhook_path or ""),
    )


async def _find(db: DbSession, workspace_id: int) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == "web")
    )


async def _ensure(db: DbSession, workspace_id: int) -> Channel:
    """The workspace's web channel, created on first read rather than by a POST.

    There is exactly one, so asking the customer to create it would be asking them to
    agree to something that has no alternative.
    """
    row = await _find(db, workspace_id)
    if row is not None:
        if not row.webhook_path:
            row.webhook_path = _new_path()
        return row

    row = Channel(
        workspace_id=workspace_id,
        kind="web",
        name="Web chat",
        webhook_path=_new_path(),
        settings_json={},
        # Off until somebody has said which pages may embed it. On-by-default with an
        # empty allowlist would refuse everything anyway, and would read as broken
        # rather than as unconfigured.
        status="disabled",
    )
    db.add(row)
    await db.flush()
    return row


def _new_path() -> str:
    """Long, random, unique. §B14 calls it an address rather than a secret."""
    return secrets.token_urlsafe(24)


@router.get("", response_model=WebChannelOut, summary="The web chat channel's settings")
async def read_settings(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> WebChannelOut:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    await db.commit()
    await db.refresh(row)
    return _out(request, row)


class WebChannelIn(BaseModel):
    enabled: bool | None = None
    allowed_origins: list[str] | None = Field(default=None, max_length=MAX_ORIGINS)
    recaptcha_site_key: str | None = Field(default=None, max_length=255)
    # Write-only. Sent as null to leave it alone, as "" to remove it, and never read
    # back - the response carries a mask instead.
    recaptcha_secret: str | None = Field(default=None, max_length=255)
    recaptcha_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


@router.put("", response_model=WebChannelOut, summary="Configure the web chat channel")
async def write_settings(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: WebChannelIn,
) -> object:
    db: DbSession = request.state.db
    row = await _ensure(db, context.id)
    sent = payload.model_dump(exclude_unset=True)
    settings = dict(row.settings_json or {})

    if "allowed_origins" in sent:
        cleaned: list[str] = []
        for raw in sent["allowed_origins"] or []:
            origin = normalise_origin(raw)
            if origin is None:
                # The same function the guard uses, so what the screen accepts and what
                # the endpoint compares can never drift apart.
                return envelope_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="invalid_origin",
                    message=f"Not an origin: {raw!r}. Write it as https://example.com, "
                    "with no path.",
                )
            if origin not in cleaned:
                cleaned.append(origin)
        settings["allowed_origins"] = cleaned

    if "recaptcha_site_key" in sent:
        settings["recaptcha_site_key"] = sent["recaptcha_site_key"] or None
    if "recaptcha_threshold" in sent and sent["recaptcha_threshold"] is not None:
        settings["recaptcha_threshold"] = sent["recaptcha_threshold"]

    if "recaptcha_secret" in sent:
        secret = sent["recaptcha_secret"]
        if secret == "":
            row.credentials_encrypted = None
        elif secret and secret[0] in _MASK_PREFIX_CHARACTERS:
            # The screen sent back what it was shown. Not an edit, and saving it would
            # replace the live secret with bullets.
            logger.info("web channel: ignored an echoed mask", extra={"channel_id": row.id})
        elif secret:
            if not key_available():
                return envelope_response(
                    status_code=status.HTTP_409_CONFLICT,
                    code="encryption_key_missing",
                    message="This installation has no ENCRYPTION_KEY, so a reCAPTCHA "
                    "secret cannot be stored. Set one and restart.",
                )
            row.credentials_encrypted = secret

    if "enabled" in sent and sent["enabled"] is not None:
        if sent["enabled"] and not settings.get("allowed_origins"):
            # Switching on with nothing allowed would refuse every visitor while the
            # screen said "on", which reads as a broken product rather than an
            # unfinished setup.
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="no_allowed_origins",
                message="Add the address of the site that will show the chat before "
                "switching it on.",
            )
        row.status = "active" if sent["enabled"] else "disabled"

    row.settings_json = settings
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "web_channel_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        # The fields, never their values: one of them is a credential and another is
        # the address the widget answers on.
        details={"channel_id": row.id, "fields": sorted(sent)},
    )
    return _out(request, row)
