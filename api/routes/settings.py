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

from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.security.permissions import WorkspaceContext, require_admin
from api.settings import store
from api.settings.registry import REGISTRY, UnknownSetting, definition

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
