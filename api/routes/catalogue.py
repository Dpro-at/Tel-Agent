"""The catalogue — §A6.11's Services tab.

What the business sells, in the words the assistant is allowed to use. Nothing ships
in here: an agent that invents a price is worse than one that says it does not know,
so the only prices it can quote are the ones somebody typed.

**The currency comes back with the list, and is not on the row.** One per workspace,
held in the settings registry as `catalogue.currency`. Returning it here saves the
screen a second request to format the first number it draws.

**A price and its mode are validated together.** `on_request` with an amount is a
contradiction the interface cannot draw, and `fixed` without one is a service the
assistant would quote as free. Both are refused rather than silently corrected -
guessing which half the caller meant is how a price ends up wrong on a call.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import NAME_MAX, PRICE_MODES, SAYS_MAX, Service
from api.security import audit
from api.security.permissions import WorkspaceContext, require_admin, require_viewer
from api.settings import store

logger = logging.getLogger("api.catalogue")

router = APIRouter(prefix="/api/catalogue", tags=["catalogue"])

CURRENCY_KEY = "catalogue.currency"

# A day. Longer than any appointment, and short enough that a stray keystroke turning
# 30 into 300000 is refused rather than read out to a caller as five months.
MINUTES_MAX = 24 * 60

# Ten million of the currency, in micros. Past any service a person books by phone,
# and inside what a 64-bit column holds with room to spare.
PRICE_MICROS_MAX = 10_000_000_000_000


class ServiceOut(BaseModel):
    id: int
    name: str
    says: str | None
    minutes: int | None
    price_mode: str
    # Null whenever `price_mode` is `on_request`, and a number otherwise.
    price_micros: int | None
    # Null means "any free", which is what most work is.
    performed_by: str | None
    bookable: bool
    position: int


class Catalogue(BaseModel):
    services: list[ServiceOut]
    # ISO 4217, so the screen can format an amount without a table of its own.
    currency: str


def _out(row: Service) -> ServiceOut:
    return ServiceOut(
        id=row.id,
        name=row.name,
        says=row.says,
        minutes=row.minutes,
        price_mode=row.price_mode,
        price_micros=row.price_micros,
        performed_by=row.performed_by,
        bookable=row.bookable,
        position=row.position,
    )


def _missing() -> object:
    """Another workspace's id must be indistinguishable from one that never existed."""
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such service in this workspace.",
    )


async def _find(db: DbSession, workspace_id: int, service_id: int) -> Service | None:
    return await db.scalar(
        select(Service).where(Service.workspace_id == workspace_id, Service.id == service_id)
    )


def _clean(value: str | None) -> str | None:
    """Whitespace is not a value. An empty field means the row does not have one."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class NewService(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    says: str | None = Field(default=None, max_length=SAYS_MAX)
    minutes: int | None = Field(default=None, ge=1, le=MINUTES_MAX)
    price_mode: str = "fixed"
    price_micros: int | None = Field(default=None, ge=0, le=PRICE_MICROS_MAX)
    performed_by: str | None = Field(default=None, max_length=NAME_MAX)
    bookable: bool = True

    @model_validator(mode="after")
    def _price_agrees_with_its_mode(self) -> NewService:
        if self.price_mode not in PRICE_MODES:
            raise ValueError(f"price_mode must be one of {', '.join(PRICE_MODES)}")
        if self.price_mode == "on_request":
            if self.price_micros is not None:
                raise ValueError("a price on request cannot also carry an amount")
        elif self.price_micros is None:
            raise ValueError("a fixed or hourly price needs an amount")
        return self


@router.get("", response_model=Catalogue, summary="What this workspace sells")
async def list_services(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> Catalogue:
    db: DbSession = request.state.db
    rows = (
        (
            await db.execute(
                select(Service)
                .where(Service.workspace_id == context.id)
                # `position` is what the business chose; `id` only settles ties, so
                # two services sharing a position never swap places between reads.
                .order_by(Service.position, Service.id)
            )
        )
        .scalars()
        .all()
    )
    currency = await store.get(db, CURRENCY_KEY, workspace_id=context.id)
    return Catalogue(services=[_out(row) for row in rows], currency=currency or "EUR")


@router.post(
    "", response_model=ServiceOut, status_code=status.HTTP_201_CREATED, summary="Add a service"
)
async def add_service(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewService,
) -> object:
    db: DbSession = request.state.db

    name = payload.name.strip()
    if not name:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_name",
            message="A service needs a name.",
        )

    # Appended, not inserted: a new service goes to the end of the list the business
    # already arranged, and moving it is a separate decision they make deliberately.
    last = await db.scalar(
        select(func.max(Service.position)).where(Service.workspace_id == context.id)
    )
    row = Service(
        workspace_id=context.id,
        name=name,
        says=_clean(payload.says),
        minutes=payload.minutes,
        price_mode=payload.price_mode,
        price_micros=payload.price_micros,
        performed_by=_clean(payload.performed_by),
        bookable=payload.bookable,
        position=(last or 0) + 1,
    )
    db.add(row)
    await db.flush()
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "service_added",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"service_id": row.id, "name": row.name},
    )
    logger.info("service %s added in workspace %s", row.id, context.id)
    return _out(row)


class EditService(BaseModel):
    """Every field optional: the screen switches `bookable` without resending a price.

    `says` and `performed_by` are nullable *and* optional, which are different things -
    absent means "leave it", and an explicit null means "this service no longer has
    one". `UNSET` is what tells them apart.
    """

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    says: str | None = Field(default=None, max_length=SAYS_MAX)
    minutes: int | None = Field(default=None, ge=1, le=MINUTES_MAX)
    price_mode: str | None = None
    price_micros: int | None = Field(default=None, ge=0, le=PRICE_MICROS_MAX)
    performed_by: str | None = Field(default=None, max_length=NAME_MAX)
    bookable: bool | None = None
    position: int | None = Field(default=None, ge=0)


@router.patch("/{service_id}", response_model=ServiceOut, summary="Change a service")
async def edit_service(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    service_id: int,
    payload: EditService,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, service_id)
    if row is None:
        return _missing()

    sent = payload.model_dump(exclude_unset=True)

    # Checked against the row as it will be, not as it was: sending only a mode has to
    # be judged against the amount already stored, or switching a fixed price to
    # "on request" would leave the old number behind for the assistant to read out.
    mode = sent.get("price_mode", row.price_mode)
    if mode not in PRICE_MODES:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_price_mode",
            message=f"price_mode must be one of {', '.join(PRICE_MODES)}.",
        )
    micros = sent.get("price_micros", row.price_micros)
    if mode == "on_request":
        # Cleared rather than refused: "on request" is a complete instruction, and the
        # amount that was there is exactly what must stop being quoted.
        micros = None
    elif micros is None:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="price_missing",
            message="A fixed or hourly price needs an amount.",
        )

    if "name" in sent:
        name = (sent["name"] or "").strip()
        if not name:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_name",
                message="A service needs a name.",
            )
        row.name = name
    if "says" in sent:
        row.says = _clean(sent["says"])
    if "minutes" in sent:
        row.minutes = sent["minutes"]
    if "performed_by" in sent:
        row.performed_by = _clean(sent["performed_by"])
    if "bookable" in sent:
        row.bookable = bool(sent["bookable"])
    if "position" in sent:
        row.position = sent["position"]
    row.price_mode = mode
    row.price_micros = micros

    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "service_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"service_id": row.id, "fields": sorted(sent)},
    )
    return _out(row)


@router.delete("/{service_id}", summary="Remove a service")
async def remove_service(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    service_id: int,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, service_id)
    if row is None:
        return _missing()

    name = row.name
    await db.delete(row)
    await db.commit()

    await audit.record(
        db,
        "service_removed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"service_id": service_id, "name": name},
    )
    logger.info("service %s removed from workspace %s", service_id, context.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
