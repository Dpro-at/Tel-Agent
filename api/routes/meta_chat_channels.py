"""Messenger and Instagram: one public door for Meta, two settings cards — §B13.

The webhook is one route family, `/public/meta/{path}`, because on Meta's side it is
one webhook mechanism with one signature scheme — the channel row found by the
address knows whether it is a page or an Instagram account, and the delivery's own
`object` word is checked against that. The guards are the WhatsApp door's, applied
identically: long random address, HMAC over the raw body, one refusal for every
reason, acknowledge first and answer after.

The two cards are one factory applied twice, because they differ only in words and
in which id the customer pastes — a page id, or the Instagram Business account id
linked to a page. Both message through the page access token (Instagram DMs travel
through the linked page), and both secrets travel as a pair with the app secret.
"""

from __future__ import annotations

import json
import logging
import secrets
from typing import Annotated

import httpx
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import meta_chat as transport
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Channel
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.meta_chat_channels")

router = APIRouter(tags=["messenger"])

# What a masked value starts with, per `api/routes/settings.py`.
_MASK_PREFIX_CHARACTERS = "•*"

# What each kind's card calls things, and which audit event a change writes.
CARD = {
    "messenger": {
        "name": "Messenger",
        "audit_event": "messenger_channel_changed",
        "tag": "messenger",
    },
    "instagram": {
        "name": "Instagram",
        "audit_event": "instagram_channel_changed",
        "tag": "instagram",
    },
}


def _refused() -> object:
    """One answer for every reason — the WhatsApp door's rule, on the second door."""
    return envelope_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="not_recognised",
        message="This address did not accept the request.",
    )


async def _channel_for(db: DbSession, path: str) -> Channel | None:
    return await db.scalar(
        select(Channel).where(
            Channel.webhook_path == path,
            Channel.kind.in_(transport.KINDS),
            Channel.status == "active",
        )
    )


@router.get("/public/meta/{path}", include_in_schema=False)
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
        logger.info("meta handshake refused", extra={"path_known": channel is not None})
        return _refused()
    return PlainTextResponse(challenge or "")


@router.post("/public/meta/{path}", include_in_schema=False)
async def receive_webhook(request: Request, path: str) -> object:
    """One delivery from Meta: verify the signature, store, acknowledge, answer later."""
    db: DbSession = request.state.db
    channel = await _channel_for(db, path)
    credentials = transport.credentials_for(channel) if channel else None
    if channel is None or credentials is None:
        logger.info("meta delivery refused", extra={"reason": "no such channel"})
        return _refused()

    raw = await request.body()
    if not transport.verify_signature(
        credentials[1], raw, request.headers.get("X-Hub-Signature-256")
    ):
        logger.info(
            "meta delivery refused",
            extra={"reason": "bad signature", "channel_id": channel.id},
        )
        return _refused()

    try:
        body = json.loads(raw)
    except ValueError:
        return _refused()

    channel_id = channel.id
    needing_reply = await transport.ingest(db, channel, body if isinstance(body, dict) else {})
    for message_id in needing_reply:
        transport.schedule_reply(request.app.state.sessionmaker, channel_id, message_id)
    return {"ok": True}


# --- The settings cards, one factory applied twice -------------------------------


class MetaChatChannelOut(BaseModel):
    enabled: bool
    # The page id for Messenger; the Instagram Business account id for Instagram.
    account_id: str | None
    # Masked, or null. Neither secret is ever read back.
    access_token_preview: str | None
    app_secret_preview: str | None
    # What Meta must be given, and what it will ask back during the handshake.
    callback_url: str
    verify_token: str
    # What the last connection test said this page or account is called.
    account_name: str | None


class MetaChatChannelIn(BaseModel):
    enabled: bool | None = None
    account_id: str | None = Field(default=None, max_length=64)
    # Write-only, as a pair - minted together in the same Meta application. Null
    # leaves the stored pair alone, "" on either removes both, a mask-echo is ignored.
    access_token: str | None = Field(default=None, max_length=512)
    app_secret: str | None = Field(default=None, max_length=255)


class TestResult(BaseModel):
    ok: bool
    account_name: str | None


def card_router(kind: str) -> APIRouter:
    """The card's four routes, bound to one kind. Two kinds, one contract."""
    words = CARD[kind]
    card = APIRouter(prefix=f"/api/channels/{kind}", tags=[words["tag"]])

    def _out(request: Request, row: Channel) -> MetaChatChannelOut:
        settings = row.settings_json or {}
        credentials = transport.credentials_for(row)
        base = str(request.base_url).rstrip("/")
        return MetaChatChannelOut(
            enabled=row.status == "active",
            account_id=settings.get("account_id"),
            access_token_preview=mask(credentials[0]) if credentials else None,
            app_secret_preview=mask(credentials[1]) if credentials else None,
            callback_url=f"{base}/public/meta/{row.webhook_path or ''}",
            verify_token=str(settings.get("verify_token") or ""),
            account_name=settings.get("account_name"),
        )

    async def _find(db: DbSession, workspace_id: int) -> Channel | None:
        return await db.scalar(
            select(Channel).where(Channel.workspace_id == workspace_id, Channel.kind == kind)
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
            kind=kind,
            name=words["name"],
            webhook_path=secrets.token_urlsafe(24),
            settings_json={"verify_token": secrets.token_urlsafe(18)},
            status="disabled",
        )
        db.add(row)
        await db.flush()
        return row

    @card.get(
        "", response_model=MetaChatChannelOut, summary=f"The {words['name']} channel's settings"
    )
    async def read_settings(
        request: Request, context: Annotated[WorkspaceContext, require_viewer]
    ) -> MetaChatChannelOut:
        db: DbSession = request.state.db
        row = await _ensure(db, context.id)
        await db.commit()
        await db.refresh(row)
        return _out(request, row)

    @card.put(
        "", response_model=MetaChatChannelOut, summary=f"Configure the {words['name']} channel"
    )
    async def write_settings(
        request: Request,
        context: Annotated[WorkspaceContext, require_admin],
        user: CurrentUser,
        payload: MetaChatChannelIn,
    ) -> object:
        db: DbSession = request.state.db
        row = await _ensure(db, context.id)
        sent = payload.model_dump(exclude_unset=True)

        if "account_id" in sent:
            settings = dict(row.settings_json or {})
            settings["account_id"] = sent["account_id"] or None
            # A different account is a different name until a test says otherwise.
            settings.pop("account_name", None)
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
                logger.info(
                    "meta channel: ignored an echoed mask", extra={"channel_id": row.id}
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
                return envelope_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="credentials_incomplete",
                    message="Send the access token and the app secret together - "
                    "they come from the same Meta application.",
                )

        if "enabled" in sent and sent["enabled"] is not None:
            if sent["enabled"] and not transport.is_ready(row):
                return envelope_response(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    code="credentials_incomplete",
                    message="Save the account id, the access token and the app "
                    "secret before switching it on.",
                )
            row.status = "active" if sent["enabled"] else "disabled"

        await db.commit()
        await db.refresh(row)

        await audit.record(
            db,
            words["audit_event"],
            request=request,
            user_id=user.id,
            username=user.username,
            # The fields, never their values: two of them are credentials.
            details={"channel_id": row.id, "fields": sorted(sent)},
        )
        return _out(request, row)

    @card.post(
        "/test",
        response_model=TestResult,
        summary=f"Prove the {words['name']} credentials work",
    )
    async def test_connection(
        request: Request, context: Annotated[WorkspaceContext, require_admin]
    ) -> object:
        db: DbSession = request.state.db
        row = await _find(db, context.id)
        credentials = transport.credentials_for(row) if row is not None else None
        account_id = transport.own_id(row) if row is not None else ""
        if row is None or credentials is None or not account_id:
            return envelope_response(
                status_code=status.HTTP_409_CONFLICT,
                code="credentials_incomplete",
                message="Save the account id, the access token and the app secret first.",
            )

        try:
            async with transport.make_client() as client:
                answer = await transport.fetch_account(client, credentials[0], account_id, kind)
        except (transport.MetaChatError, httpx.HTTPError) as error:
            logger.info(
                "meta test failed", extra={"channel_id": row.id, "error": str(error)[:200]}
            )
            return envelope_response(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="meta_refused",
                message="Meta did not accept these credentials. Check the token, the "
                "app secret and the account id in your Meta application.",
            )

        name = answer.get("name") or answer.get("username")
        row.settings_json = {**(row.settings_json or {}), "account_name": name}
        await db.commit()
        return TestResult(ok=True, account_name=name)

    return card


messenger_card = card_router("messenger")
instagram_card = card_router("instagram")
