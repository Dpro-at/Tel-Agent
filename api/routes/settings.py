"""The settings endpoints the eleven-tab screen consumes.

Two scopes, two guards. Installation settings — the mail server, the hostname — are an
**admin** decision: they affect every workspace on the machine. A workspace's own
settings are also admin, but only within that workspace, which `require_admin` already
scopes through the membership row.

**A secret is never returned in full.** Reads come back masked, and a write that sends
the mask back is ignored rather than saved: the settings screen renders `••••3ab1` in
the field, and a user who edits an unrelated tab and presses save must not overwrite
their live credential with four bullets. That is E4's acceptance condition, arriving
here with the store that makes it possible.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.security.permissions import WorkspaceContext, require_admin
from api.settings import store
from api.settings.registry import REGISTRY, UnknownSetting, definition

logger = logging.getLogger("api.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

# What a masked value looks like coming back from the client.
#
# `mask()` keeps the last four characters — `••••3ab1` — so "is it all bullets" is the
# wrong question and was the first version of this check: it never matched, and the
# mask would have been saved straight over the live credential. The right question is
# whether the value *starts* with the bullets a mask begins with; a real secret cannot,
# because nobody types them.
_MASK_PREFIX_CHARACTERS = "•*"


def _encryption_available() -> bool:
    """Whether this installation can encrypt at all - see `crypto.key_available`."""
    from api.security import crypto

    return crypto.key_available()


def _is_echoed_mask(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return value[0] in _MASK_PREFIX_CHARACTERS


class SettingEntry(BaseModel):
    key: str
    value: Any
    scope: str
    kind: str
    secret: bool
    description: str


class SettingsWrite(BaseModel):
    values: dict[str, Any] = Field(
        description="Declared keys only. A secret sent back as its mask is ignored, "
        "so saving an unrelated tab cannot overwrite a stored credential."
    )


@router.get("", summary="Every declared setting, secrets masked")
async def read_settings(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> list[SettingEntry]:
    db: DbSession = request.state.db
    values = await store.all_for(db, workspace_id=context.id)
    return [
        SettingEntry(
            key=key,
            value=values[key],
            scope=spec.scope,
            kind=spec.kind,
            secret=spec.secret,
            description=spec.description,
        )
        for key, spec in REGISTRY.items()
    ]


@router.patch("", summary="Write settings")
async def write_settings(
    request: Request,
    payload: SettingsWrite,
    context: Annotated[WorkspaceContext, require_admin],
) -> object:
    db: DbSession = request.state.db
    written: list[str] = []
    ignored: list[str] = []

    for key, value in payload.values.items():
        try:
            spec = definition(key)
        except UnknownSetting as error:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="unknown_setting",
                message=str(error),
            )

        if spec.secret and not _encryption_available():
            # A designed answer, not a traceback. An installation with no
            # ENCRYPTION_KEY cannot store a credential at all (§B9.2), and the person
            # typing an SMTP password into a form needs to be told that - not shown
            # "something went wrong" while the reason sits in a log they cannot read.
            return envelope_response(
                status_code=status.HTTP_409_CONFLICT,
                code="encryption_key_missing",
                message=(
                    "This installation has no ENCRYPTION_KEY, so credentials cannot be "
                    "stored. Generate one with `openssl rand -hex 32`, put it in .env, "
                    "and restart. Keep a copy somewhere that is not the backup."
                ),
            )

        if spec.secret and _is_echoed_mask(value):
            ignored.append(key)
            continue

        if value is None or value == "":
            # An emptied field means "use the default", which is the absence of a row
            # rather than a stored empty string.
            await store.clear(
                db, key, workspace_id=None if spec.scope == "installation" else context.id
            )
        else:
            await store.set_value(
                db,
                key,
                value,
                workspace_id=None if spec.scope == "installation" else context.id,
            )
        written.append(key)

    await db.commit()
    return {"written": written, "ignored_masked": ignored}


class MailTested(BaseModel):
    sent: bool
    # Where it went - the admin's own address, so "check your inbox" has a referent.
    to: str


@router.post(
    "/mail/test",
    response_model=MailTested,
    summary="Send a test email to the signed-in account",
)
async def send_test_mail(
    request: Request, context: Annotated[WorkspaceContext, require_admin], user: CurrentUser
) -> object:
    """Prove the saved configuration can actually deliver, before anyone relies on it.

    To the *signed-in admin's own address*, never a typed one: a form that mails an
    arbitrary address on demand is a spam relay with extra steps. Failures come back
    as a designed answer, not a 502 traceback - the screen's stale state exists
    precisely because "the setting looks right" is what a broken mail server shows.
    """
    import asyncio

    from api import mail

    db: DbSession = request.state.db
    config = await mail.resolve(db, request.app.state.settings)
    if not config.configured:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="mail_not_configured",
            message="No mail server is configured. Fill in the host and sender above, "
            "save, then test.",
        )
    if not user.email:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="no_email_on_account",
            message="Your account has no email address, so there is nowhere to send the test.",
        )

    # `smtplib` blocks; a slow mail server must not stall every other request.
    sent = await asyncio.to_thread(
        mail.send,
        config,
        to=user.email,
        subject="Tel-Agent test message",
        body="This is a test message from your Tel-Agent installation. "
        "If it reached you, outgoing mail is working.",
    )
    if not sent:
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="mail_failed",
            message="The mail server refused or did not answer. The log carries its "
            "exact words.",
        )
    return MailTested(sent=True, to=user.email)


class ModelTested(BaseModel):
    reached: bool
    # What answered, so "it works" has a referent when two people share an installation
    # and one of them has just repointed it at their laptop.
    model: str
    base_url: str


@router.post(
    "/llm/test",
    response_model=ModelTested,
    summary="Ask the configured model for one token, and report what happened",
)
async def test_model(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> object:
    """Prove the saved key reaches a model, before a visitor is the one who finds out.

    **One token, then the stream is closed.** The point is reachability - the endpoint
    answers, the key is accepted, the model name exists - and none of that needs a
    finished sentence. Closing early also exercises the cancellation path that Rule 3
    says must work, on the cheapest request in the product.

    Failures come back as a designed answer with the provider's own status, not a 502
    traceback: "the setting looks right" is exactly what a rejected key shows.
    """
    from contextlib import aclosing

    import httpx

    from agent.config import ConfigurationError
    from agent.providers.llm import provider_for
    from api import llm
    from api.security.crypto import DecryptionFailed

    db: DbSession = request.state.db

    try:
        settings = await llm.resolve(db)
    except ConfigurationError as broken:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="llm_incomplete",
            message=str(broken),
        )
    except DecryptionFailed:
        # This is the button somebody presses when credentials stop working, so it is
        # the least useful place in the product to answer with a traceback.
        logger.exception("the stored model key could not be decrypted")
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="llm_key_unreadable",
            message=llm.UNREADABLE_KEY,
        )
    if settings is None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="llm_not_configured",
            message="No model is connected. Choose a provider, fill in the model and "
            "the key above, save, then test.",
        )

    provider = provider_for(settings)
    try:
        async with aclosing(provider.stream([{"role": "user", "content": "ping"}])) as stream:
            async for _event in stream:
                # An empty stream is a valid answer to "say nothing" (§B3), so arriving
                # here is not what proves the endpoint works - returning without an
                # exception is. This breaks only so the request is not paid in full.
                break
    except httpx.HTTPStatusError as refused:
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="llm_refused",
            message=f"The endpoint answered {refused.response.status_code}. "
            "A 401 is the key, a 404 is usually the model name or the address.",
        )
    except (httpx.HTTPError, httpx.InvalidURL) as unreachable:
        # The exception's text, not the request's: httpx puts the URL in some of these
        # and the URL is the one part of a model configuration worth not echoing back
        # into a log line beside everything else.
        logger.warning(
            "the model endpoint could not be reached",
            extra={"error": type(unreachable).__name__},
        )
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="llm_unreachable",
            message="Nothing answered at that address. Check the endpoint, and that "
            "this machine can reach it.",
        )

    return ModelTested(reached=True, model=settings.model, base_url=settings.base_url)


class CalendarTested(BaseModel):
    reached: bool
    # The collection address that answered - configuration, not a secret; the
    # username and password stay server-side, like everywhere else on this screen.
    source: str


@router.post(
    "/calendar/test",
    response_model=CalendarTested,
    summary="Ask the configured calendar for one free-busy day, and report what happened",
)
async def test_calendar(
    request: Request, context: Annotated[WorkspaceContext, require_admin]
) -> object:
    """Prove the saved CalDAV credentials reach a calendar, before a caller finds out.

    The probe is the cheapest real question the provider can ask: one day's free-busy
    report. Reachability, sign-in and "this URL is actually a calendar collection" are
    all proven by it; what the busy periods say does not matter here.

    The same three refusals the availability endpoint distinguishes, as designed
    answers rather than tracebacks - this is the button somebody presses when
    credentials stop working.
    """
    import datetime as dt

    from agent.providers.calendar import CalDAVCalendar, CalendarError
    from api.settings import store as settings_store

    db: DbSession = request.state.db

    url = str(
        await settings_store.get(db, "calendar.caldav_url", workspace_id=context.id) or ""
    ).strip()
    if not url:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="calendar_not_configured",
            message="No calendar is connected. Fill in the CalDAV address and the app "
            "password above, save, then test.",
        )
    username = str(
        await settings_store.get(db, "calendar.caldav_username", workspace_id=context.id) or ""
    )
    password = str(
        await settings_store.get(db, "calendar.caldav_password", workspace_id=context.id) or ""
    )

    calendar = CalDAVCalendar(url=url, username=username, password=password)
    start = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        await calendar.busy(start, start + dt.timedelta(days=1))
    except CalendarError as error:
        logger.warning("the calendar test failed: %s", error)
        if error.status in (401, 403):
            return envelope_response(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="calendar_refused",
                message=f"The calendar server answered {error.status}. The username or "
                "the app password is most likely wrong or revoked.",
            )
        if error.status is not None:
            # The server answered, just not with a free-busy report - a 404 is usually
            # a URL that is not a calendar collection, a 500 is the server's own day.
            return envelope_response(
                status_code=status.HTTP_502_BAD_GATEWAY,
                code="calendar_unreachable",
                message=f"The server answered {error.status} instead of a free-busy "
                "report. Check that the URL points at a calendar collection.",
            )
        return envelope_response(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code="calendar_unreachable",
            message="Nothing answered at that address. Check the URL, and that this "
            "machine can reach the calendar server.",
        )

    return CalendarTested(reached=True, source=url)
