"""The numbers registry — which phone numbers this workspace holds.

**A registry, not a phone system.** Milestone 11 is where a number is actually
answered; what exists before it is the record the routing rules and the calls
screen will point at. So the surface is deliberately small: list, add, disable,
release. Nothing here claims a number *works* — a status that cannot be measured
is not drawn as if it could be (the health screen's own rule).

**SIP credentials are not accepted yet, and that is §B9.2 doing its job.** The
`numbers.sip_config` column is plain JSON, and per-number SIP credentials are
user-entered secrets, which must live in encrypted columns. Taking them today
would store them in the clear; they arrive with the SIP milestone, encrypted,
when their real shape is known. Until then a number is what a person can safely
type: the E.164 and who provides it.

**`owner` is §B5 decision 3.** Every number added here is `customer` — users
bring their own number in v1, and reselling belongs to Tel-Agent Cloud. The
release guard still checks it: a platform-held number must never be releasable
from a self-hosted dashboard, and the rule is cheaper to write now than to
retrofit after both kinds exist.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import Number
from api.security import audit
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.numbers")

router = APIRouter(prefix="/api/numbers", tags=["numbers"])

# E.164: a plus, then seven to fifteen digits, no leading zero. Spaces and dashes are
# stripped before the check - people paste numbers the way their provider printed them.
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class NumberOut(BaseModel):
    id: int
    e164: str
    provider: str
    owner: str
    status: str
    created_at: str


def _out(row: Number) -> NumberOut:
    return NumberOut(
        id=row.id,
        e164=row.e164,
        provider=row.provider,
        owner=row.owner,
        status=row.status,
        created_at=row.created_at.isoformat(),
    )


def _missing() -> object:
    """A foreign id must be indistinguishable from one that does not exist."""
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such number in this workspace.",
    )


async def _row(db: DbSession, workspace_id: int, number_id: int) -> Number | None:
    return await db.scalar(
        select(Number).where(Number.workspace_id == workspace_id, Number.id == number_id)
    )


@router.get("", response_model=list[NumberOut], summary="The numbers this workspace holds")
async def list_numbers(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> list[NumberOut]:
    db: DbSession = request.state.db
    rows = (
        (
            await db.execute(
                select(Number)
                .where(Number.workspace_id == context.id)
                .order_by(Number.created_at, Number.id)
            )
        )
        .scalars()
        .all()
    )
    return [_out(row) for row in rows]


class NewNumber(BaseModel):
    e164: str = Field(min_length=4, max_length=25)
    provider: str = Field(min_length=1, max_length=64)


@router.post(
    "",
    response_model=NumberOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a number",
)
async def add_number(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewNumber,
) -> object:
    db: DbSession = request.state.db

    e164 = re.sub(r"[\s\-()]", "", payload.e164)
    if not _E164.fullmatch(e164):
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_e164",
            message="A number is written as +, country code, digits - like +43664123456.",
        )
    provider = payload.provider.strip()
    if not provider:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_provider",
            message="Say which provider holds this number.",
        )

    clash = await db.scalar(
        select(Number).where(Number.workspace_id == context.id, Number.e164 == e164)
    )
    if clash is not None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="number_taken",
            message="This workspace already has that number.",
        )

    row = Number(
        workspace_id=context.id,
        provider=provider,
        owner="customer",
        e164=e164,
        status="active",
    )
    db.add(row)
    await db.flush()
    answer = _out(row)
    # Read before the commit expires them - an async session will not refresh lazily.
    acting_id, acting_name = user.id, user.username
    await db.commit()
    await audit.record(
        db,
        "number_added",
        request=request,
        user_id=acting_id,
        username=acting_name,
        details={"workspace_id": context.id, "e164": e164, "provider": provider},
    )
    logger.info("number added", extra={"workspace_id": context.id, "number_id": answer.id})
    return answer


class StatusChange(BaseModel):
    status: str


@router.patch("/{number_id}", response_model=NumberOut, summary="Enable or disable a number")
async def change_status(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    number_id: int,
    payload: StatusChange,
) -> object:
    db: DbSession = request.state.db

    if payload.status not in ("active", "disabled"):
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_status",
            message="A number is either active or disabled.",
        )

    row = await _row(db, context.id, number_id)
    if row is None:
        return _missing()

    was = row.status
    row.status = payload.status
    answer = _out(row)
    acting_id, acting_name = user.id, user.username
    await db.commit()
    await audit.record(
        db,
        "number_status_changed",
        request=request,
        user_id=acting_id,
        username=acting_name,
        details={
            "workspace_id": context.id,
            "e164": answer.e164,
            "from": was,
            "to": answer.status,
        },
    )
    logger.info(
        "number status changed",
        extra={"workspace_id": context.id, "number_id": number_id, "status": answer.status},
    )
    return answer


@router.delete("/{number_id}", summary="Release a number")
async def release_number(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    number_id: int,
) -> object:
    """Removes the record here - the provider contract is the customer's own affair.

    The dashboard cannot cancel a number with the provider and does not pretend to;
    what release means is that Tel-Agent stops knowing about it.
    """
    db: DbSession = request.state.db

    row = await _row(db, context.id, number_id)
    if row is None:
        return _missing()

    if row.owner != "customer":
        # §B5 decision 3: who holds the number governs who may release it. A
        # platform-held number is Tel-Agent Cloud's to release, not this dashboard's.
        return envelope_response(
            status_code=status.HTTP_403_FORBIDDEN,
            code="platform_number",
            message="This number is held by the platform and cannot be released here.",
        )

    e164 = row.e164
    acting_id, acting_name = user.id, user.username
    await db.delete(row)
    await db.commit()
    await audit.record(
        db,
        "number_released",
        request=request,
        user_id=acting_id,
        username=acting_name,
        details={"workspace_id": context.id, "e164": e164},
    )
    logger.info("number released", extra={"workspace_id": context.id, "number_id": number_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
