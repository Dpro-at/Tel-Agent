"""Webhooks — the registry, and the one moment its secret is visible.

**The secret is returned in full exactly once**, in the response that creates the
webhook, and masked in every read afterwards (§B9, E3). That is not a convenience: a
signing secret has to reach the receiving system somehow, and the alternatives are
worse. Storing it plainly so it can be re-read makes every later database dump a leak;
letting the operator choose it gets a chosen password. Shown once, the copy happens
while the person is already looking at the screen.

**Rotating replaces it and returns the new one once**, on the same terms. A hook that
is switched off keeps its secret, because a hook switched off during an incident should
come back without every receiver being reconfigured.

**Sending lives in `api/webhooks.py` and the background runner.** This records what a
webhook is; deliveries, retries and their statuses ride on the job that sends them.
The one delivery made from here is the test one - fired by hand from the settings
screen, synchronously, because the person clicking wants the receiver's answer and
not a promise that a job exists.
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import WEBHOOK_EVENTS, Webhook
from api.security import audit
from api.security.crypto import key_available, mask
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# 32 bytes of randomness, hex. Long enough that guessing is not a strategy, and plain
# ASCII so it survives being pasted through whatever configuration file receives it.
_SECRET_BYTES = 32


def _new_secret() -> str:
    return secrets.token_hex(_SECRET_BYTES)


def _no_key() -> object:
    """Asked before the write, not discovered inside it.

    Without a key the INSERT raises, and the exception carries the SQLAlchemy
    parameter dump - which is the secret - into the log. Refusing here is what keeps
    a missing key from becoming a leaked one.
    """
    return envelope_response(
        status_code=status.HTTP_409_CONFLICT,
        code="encryption_key_missing",
        message="This installation has no ENCRYPTION_KEY, so a signing secret "
        "cannot be stored. Set one and restart.",
    )


class WebhookOut(BaseModel):
    id: int
    name: str | None
    url: str
    events: list[str]
    enabled: bool
    # Masked - the last four characters, per §B9. Never the secret itself.
    secret_preview: str
    created_at: str
    updated_at: str


class WebhookCreated(WebhookOut):
    """The create and rotate responses, which carry the secret once and never again."""

    secret: str


def _out(row: Webhook) -> WebhookOut:
    return WebhookOut(
        id=row.id,
        name=row.name,
        url=row.url,
        events=list(row.events or []),
        enabled=row.enabled,
        secret_preview=mask(row.secret),
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _missing() -> object:
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such webhook in this workspace.",
    )


def _checked_url(raw: str) -> str | None:
    """An absolute http(s) URL with a host, or nothing.

    `https` is not required: an installation posting to another service on its own LAN
    has no certificate to present, and refusing that would push people to a public
    endpoint - which is the outcome the rule was meant to prevent.
    """
    parsed = urlparse(raw.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return raw.strip()


def _checked_events(events: list[str]) -> object | None:
    unknown = sorted(set(events) - set(WEBHOOK_EVENTS))
    if unknown:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="unknown_event",
            message=f"Not an event this product sends: {', '.join(unknown)}.",
        )
    if not events:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="no_events",
            message="A webhook that is told about nothing would never fire.",
        )
    return None


async def _find(db: DbSession, workspace_id: int, webhook_id: int) -> Webhook | None:
    return await db.scalar(
        select(Webhook).where(Webhook.workspace_id == workspace_id, Webhook.id == webhook_id)
    )


@router.get("/events", response_model=list[str], summary="The events a webhook can ask for")
async def list_events(context: Annotated[WorkspaceContext, require_viewer]) -> list[str]:
    """The vocabulary, so the screen does not carry a second copy of it."""
    return list(WEBHOOK_EVENTS)


@router.get("", response_model=list[WebhookOut], summary="The webhooks in this workspace")
async def list_webhooks(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> list[WebhookOut]:
    db: DbSession = request.state.db
    rows = (
        (
            await db.execute(
                select(Webhook)
                .where(Webhook.workspace_id == context.id)
                .order_by(Webhook.created_at, Webhook.id)
            )
        )
        .scalars()
        .all()
    )
    return [_out(row) for row in rows]


class NewWebhook(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    events: list[str]
    name: str | None = Field(default=None, max_length=80)


@router.post(
    "",
    response_model=WebhookCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Add a webhook, and see its secret once",
)
async def add_webhook(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewWebhook,
) -> object:
    db: DbSession = request.state.db

    if not key_available():
        return _no_key()

    url = _checked_url(payload.url)
    if url is None:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_url",
            message="A webhook needs an absolute http:// or https:// URL.",
        )
    refused = _checked_events(payload.events)
    if refused is not None:
        return refused

    secret = _new_secret()
    row = Webhook(
        workspace_id=context.id,
        url=url,
        name=payload.name,
        events=payload.events,
        secret=secret,
    )
    db.add(row)
    await db.flush()
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "webhook_added",
        request=request,
        user_id=user.id,
        username=user.username,
        # The URL, never the secret. An audit trail is read by more people than the
        # screen that created the row.
        details={"webhook_id": row.id, "url": row.url, "events": list(row.events)},
    )
    logger.info("webhook %s added in workspace %s", row.id, context.id)
    return WebhookCreated(**_out(row).model_dump(), secret=secret)


class EditWebhook(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=500)
    events: list[str] | None = None
    name: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None


@router.patch("/{webhook_id}", response_model=WebhookOut, summary="Edit a webhook")
async def edit_webhook(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    webhook_id: int,
    payload: EditWebhook,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, webhook_id)
    if row is None:
        return _missing()

    sent = payload.model_dump(exclude_unset=True)

    if "url" in sent:
        url = _checked_url(sent["url"] or "")
        if url is None:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_url",
                message="A webhook needs an absolute http:// or https:// URL.",
            )
        row.url = url
    if "events" in sent:
        refused = _checked_events(sent["events"] or [])
        if refused is not None:
            return refused
        row.events = sent["events"]
    if "name" in sent:
        row.name = sent["name"]
    if "enabled" in sent:
        row.enabled = sent["enabled"]

    row.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "webhook_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"webhook_id": row.id, "fields": sorted(sent)},
    )
    return _out(row)


@router.post(
    "/{webhook_id}/secret",
    response_model=WebhookCreated,
    summary="Replace the secret, and see the new one once",
)
async def rotate_secret(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    webhook_id: int,
) -> object:
    db: DbSession = request.state.db
    if not key_available():
        return _no_key()

    row = await _find(db, context.id, webhook_id)
    if row is None:
        return _missing()

    secret = _new_secret()
    row.secret = secret
    row.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "webhook_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"webhook_id": row.id, "fields": ["secret"]},
    )
    logger.info("webhook %s secret rotated in workspace %s", row.id, context.id)
    return WebhookCreated(**_out(row).model_dump(), secret=secret)


class TestResult(BaseModel):
    """What the receiver did with the test, told plainly either way."""

    delivered: bool
    # The HTTP answer, when there was one. A refusal (anything outside 2xx) still has
    # a code; a receiver that could not be reached at all has none.
    status_code: int | None
    error: str | None


@router.post(
    "/{webhook_id}/test",
    response_model=TestResult,
    summary="Send one test delivery, signed like a real one",
)
async def send_test(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    webhook_id: int,
) -> object:
    """Fired by hand from the settings screen, so the operator can prove the receiver
    is wired - URL, secret, signature check - before anything real depends on it.

    Synchronous on purpose, unlike every real delivery: the person clicking wants the
    receiver's answer, and a queued job would answer "maybe, later". Signed exactly
    like a real delivery so the receiver's verification code is what gets tested, with
    `webhook.test` as the event name so it can tell the drill from the fire.
    """
    from api import webhooks as sender

    db: DbSession = request.state.db
    row = await _find(db, context.id, webhook_id)
    if row is None:
        return _missing()

    try:
        answered = await sender.send(
            row,
            event="webhook.test",
            data={"note": "A test delivery, sent by hand from the settings screen."},
            delivery_id=0,
            now=dt.datetime.now(dt.UTC),
        )
    except httpx.HTTPError as error:
        result = TestResult(delivered=False, status_code=None, error=str(error)[:300])
    except RuntimeError as error:
        # `send` raises this on any non-2xx answer, redirects included; the code is
        # the last word of its message. Parsed rather than re-requested: asking the
        # receiver twice to report once would double every side effect it has.
        code = str(error).rsplit(" ", 1)[-1]
        result = TestResult(
            delivered=False,
            status_code=int(code) if code.isdigit() else None,
            error=str(error)[:300],
        )
    else:
        result = TestResult(delivered=True, status_code=answered.status_code, error=None)

    logger.info(
        "webhook %s test delivery: %s",
        row.id,
        "delivered" if result.delivered else result.error,
    )
    return result


@router.delete("/{webhook_id}", summary="Remove a webhook")
async def remove_webhook(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    webhook_id: int,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, webhook_id)
    if row is None:
        return _missing()

    url = row.url
    await db.delete(row)
    await db.commit()

    await audit.record(
        db,
        "webhook_removed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"webhook_id": webhook_id, "url": url},
    )
    logger.info("webhook %s removed from workspace %s", webhook_id, context.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
