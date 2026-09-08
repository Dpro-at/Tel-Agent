"""One settings card's routes, for every channel that declares a descriptor.

The eight hand-written cards stay as they are (D-044). Everything after them is
served from here: the descriptor in `module.SETUP` says what the card shows, and this
file enforces the same §B9 write rules `api/routes/discord_channel.py` enforces by
hand — a secret goes in and only a mask comes out, an empty string clears it and
switches the channel off, a value that starts like a mask is an echo and is ignored,
and nothing is written at all when the installation has no encryption key.

The one thing this route does that a hand-written card never had to: a module may
declare `activate`, the call that tells the platform where to reach us. It runs after
the operator switches the channel on, and a refusal leaves the channel off — a channel
the platform has never heard of is not switched on, it is only marked so.
"""

from __future__ import annotations

import logging
import secrets
from types import ModuleType
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import generic
from api.channels.setup import Setup
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.generic_channel")

router = APIRouter(prefix="/api/channels", tags=["channels"])

# What a masked value starts with, per `api/routes/settings.py`.
_MASK_PREFIX_CHARACTERS = "•*"

# What the last connection test said this channel is, kept beside the shown fields.
_IDENTITY = "identity"


class GenericChannelOut(BaseModel):
    # The descriptor, exactly as the module declares it. Metadata only: there is
    # nowhere in it for a credential to travel.
    setup: dict[str, Any]
    enabled: bool
    status: str
    # The shown fields, as stored. Secrets are in `previews`, masked or null.
    values: dict[str, str]
    previews: dict[str, str | None]
    identity: str | None
    # Where the platform must call, for a channel that receives webhooks.
    webhook_url: str | None
    verified_live: bool


class GenericChannelIn(BaseModel):
    enabled: bool | None = None
    # Write-only per field: "" removes, a mask-echo is ignored, anything else is stored.
    fields: dict[str, str] | None = None


class TestResult(BaseModel):
    ok: bool
    identity: str | None


def _unknown(kind: str) -> object:
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="unknown_channel",
        message=f"This installation has no channel of kind {kind!r}.",
    )


def _missing(names: tuple[str, ...], setup: Setup) -> object:
    declared = {field.name: field.label for field in setup.fields}
    labels = ", ".join(declared.get(name, name) for name in names)
    return envelope_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="missing_credentials",
        message=f"Save {labels} before switching this channel on.",
    )


async def _find(db: DbSession, workspace_id: int, kind: str) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == kind)
    )


async def _ensure(db: DbSession, workspace_id: int, module: ModuleType) -> Channel:
    row = await _find(db, workspace_id, module.KIND)
    is_door = getattr(module, "INBOUND", "") == "door"
    if row is not None:
        if is_door and not row.webhook_path:
            row.webhook_path = secrets.token_urlsafe(24)
        return row
    row = Channel(
        workspace_id=workspace_id,
        kind=module.KIND,
        name=module.SETUP.title,
        # A long random address, minted once and unique per channel - the door's own
        # first guard, and the reason an unknown path is indistinguishable from a
        # disabled one.
        webhook_path=secrets.token_urlsafe(24) if is_door else None,
        settings_json={},
        status="disabled",
    )
    db.add(row)
    await db.flush()
    return row


def _out(request: Request, module: ModuleType, row: Channel) -> GenericChannelOut:
    setup: Setup = module.SETUP
    stored = generic.secrets_of(row)
    base = str(request.base_url).rstrip("/")
    is_door = getattr(module, "INBOUND", "") == "door"
    return GenericChannelOut(
        setup=setup.public(),
        enabled=row.status == "active",
        status=row.status,
        values=generic.shown_of(row),
        previews={
            name: (mask(stored[name]) if stored.get(name) else None)
            for name in setup.secret_names()
        },
        identity=(row.settings_json or {}).get(_IDENTITY),
        webhook_url=f"{base}/public/{row.kind}/{row.webhook_path or ''}" if is_door else None,
        verified_live=setup.verified_live,
    )


def _forget_identity(row: Channel) -> None:
    """A changed credential is a different account until a test says otherwise."""
    settings = dict(row.settings_json or {})
    settings.pop(_IDENTITY, None)
    row.settings_json = settings


@router.get("/{kind}", response_model=GenericChannelOut, summary="One channel's settings")
async def read_settings(
    request: Request, kind: str, context: Annotated[WorkspaceContext, require_viewer]
) -> object:
    module = generic.module_for(kind)
    if module is None:
        return _unknown(kind)
    db: DbSession = request.state.db
    row = await _ensure(db, context.id, module)
    await db.commit()
    await db.refresh(row)
    return _out(request, module, row)


@router.put("/{kind}", response_model=GenericChannelOut, summary="Configure one channel")
async def write_settings(
    request: Request,
    kind: str,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: GenericChannelIn,
) -> object:
    module = generic.module_for(kind)
    if module is None:
        return _unknown(kind)
    setup: Setup = module.SETUP

    db: DbSession = request.state.db
    row = await _ensure(db, context.id, module)
    sent = payload.model_dump(exclude_unset=True)
    written = dict(sent.get("fields") or {})

    if written:
        stored_secrets = generic.secrets_of(row)
        stored_shown = generic.shown_of(row)
        arriving: dict[str, str] = {}

        for name, raw in written.items():
            field = setup.field(name)
            if field is None:
                logger.info(
                    "channel: ignored a field the descriptor does not declare",
                    extra={"channel_id": row.id, "kind": kind, "field": name},
                )
                continue
            value = "" if raw is None else str(raw)
            if value and value[0] in _MASK_PREFIX_CHARACTERS:
                logger.info(
                    "channel: ignored an echoed mask",
                    extra={"channel_id": row.id, "kind": kind, "field": name},
                )
                continue
            if value == "":
                # An empty string removes. A field left alone is simply not sent.
                (stored_secrets if field.secret else stored_shown).pop(name, None)
            elif field.secret:
                # Held back until the whole batch is known to be storable, so a
                # missing key refuses the request rather than half-applying it.
                arriving[name] = value
            else:
                stored_shown[name] = value

        if arriving and not key_available():
            return envelope_response(
                status_code=status.HTTP_409_CONFLICT,
                code="encryption_key_missing",
                message="This installation has no ENCRYPTION_KEY, so a credential "
                "cannot be stored. Set one and restart.",
            )

        stored_secrets.update(arriving)
        generic.store_secrets(row, stored_secrets)
        generic.store_shown(row, stored_shown)
        _forget_identity(row)
        # A credential that is gone takes the channel down with it, rather than
        # leaving a channel switched on that cannot answer anybody.
        if generic.missing_fields(row, setup):
            row.status = "disabled"

    if "enabled" in sent and sent["enabled"] is not None:
        if sent["enabled"]:
            absent = generic.missing_fields(row, setup)
            if absent:
                return _missing(absent, setup)
            row.status = "active"
        else:
            row.status = "disabled"

    # Only when something actually changed: re-announcing an untouched channel to the
    # platform on every read-modify-write would be a call nobody asked for.
    changed = bool(written) or "enabled" in sent
    refused = (
        await _activate(request, module, row) if changed and row.status == "active" else None
    )

    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "channel_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        # The field names, never their values: some of them are credentials.
        details={
            "channel_id": row.id,
            "kind": kind,
            "fields": sorted([*written, *(["enabled"] if "enabled" in sent else [])]),
        },
    )
    return refused or _out(request, module, row)


async def _activate(request: Request, module: ModuleType, row: Channel) -> object | None:
    """Tell the platform where to reach us, for the channels that have to be told.

    Only when the module declares it, and only for a channel that is switched on. A
    refusal switches the channel back off before anything is committed: a channel the
    platform has never heard of is not a working channel.
    """
    activate = getattr(module, "activate", None)
    if activate is None:
        return None

    base = str(request.base_url).rstrip("/")
    webhook_url = (
        f"{base}/public/{row.kind}/{row.webhook_path or ''}"
        if getattr(module, "INBOUND", "") == "door"
        else None
    )
    try:
        async with module.make_client() as client:
            await activate(client, generic.credentials_of(row), webhook_url)
    except (generic.ChannelRefused, httpx.HTTPError) as error:
        logger.info(
            "channel activation refused",
            extra={"channel_id": row.id, "kind": row.kind, "error": str(error)[:200]},
        )
        row.status = "disabled"
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=f"{row.kind}_refused",
            message="The platform did not accept these credentials. Check them in "
            "your own developer account for this platform.",
        )
    return None


@router.post(
    "/{kind}/test", response_model=TestResult, summary="Prove one channel's credentials work"
)
async def test_connection(
    request: Request, kind: str, context: Annotated[WorkspaceContext, require_admin]
) -> object:
    module = generic.module_for(kind)
    if module is None:
        return _unknown(kind)
    setup: Setup = module.SETUP

    db: DbSession = request.state.db
    row = await _find(db, context.id, kind)
    if row is None or generic.missing_fields(row, setup):
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="missing_credentials",
            message="Save this channel's credentials first.",
        )

    try:
        async with module.make_client() as client:
            identity = await module.probe(client, generic.credentials_of(row))
    except (generic.ChannelRefused, httpx.HTTPError) as error:
        logger.info(
            "channel test failed",
            extra={"channel_id": row.id, "kind": kind, "error": str(error)[:200]},
        )
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=f"{kind}_refused",
            message="The platform did not accept these credentials. Check them in "
            "your own developer account for this platform.",
        )

    row.settings_json = {**(row.settings_json or {}), _IDENTITY: identity}
    await db.commit()
    return TestResult(ok=True, identity=identity)
