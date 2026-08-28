"""Contacts — the phonebook, read and kept.

**Reception writes here, and that is the point of the role.** Numbers and rules
are configuration and stay admin's; a contact's name and note are the front
desk's daily material. Viewer still reads.

**"Last heard from" comes out of the archive**, matched on the number, the same
way the rules screen answers it - so a contact row can say when this person last
reached the business without anything having to maintain a counter.

Matching a live caller to a contact and writing `conversations.contact_id` is the
agent's job when calls arrive; nothing here pretends to have done it.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.errors import envelope_response
from api.models import Contact, Conversation
from api.security.permissions import WorkspaceContext, require_reception, require_viewer

logger = logging.getLogger("api.contacts")

router = APIRouter(prefix="/api/contacts", tags=["contacts"])

PAGE = 50

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class ContactOut(BaseModel):
    id: int
    e164: str
    name: str
    tags: list[str]
    notes: str | None
    created_at: str
    # When this number last reached the business, out of the archive. Null when the
    # archive has never seen it.
    last_heard_at: str | None = None


class ContactPage(BaseModel):
    contacts: list[ContactOut]
    has_more: bool


def _out(row: Contact) -> ContactOut:
    return ContactOut(
        id=row.id,
        e164=row.e164,
        name=row.name,
        tags=[str(tag) for tag in (row.tags or [])],
        notes=row.notes,
        created_at=row.created_at.isoformat(),
    )


def _missing() -> object:
    """A foreign id must be indistinguishable from one that does not exist."""
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such contact in this workspace.",
    )


def _normalised(raw: str) -> str | None:
    cleaned = re.sub(r"[\s\-()]", "", raw)
    return cleaned if _E164.fullmatch(cleaned) else None


def _cleaned_tags(raw: list[str] | None) -> list[str]:
    """Trimmed, deduplicated in order, empty words dropped."""
    seen: list[str] = []
    for tag in raw or []:
        word = tag.strip()
        if word and word not in seen:
            seen.append(word)
    return seen[:12]


@router.get("", response_model=ContactPage, summary="The contacts in this workspace")
async def list_contacts(
    request: Request,
    context: Annotated[WorkspaceContext, require_viewer],
    q: Annotated[str | None, Query(max_length=120)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ContactPage:
    """Alphabetical, searched by name or number on the server - the list is a page
    of a capped size, so a browser-side filter would quietly hide the rest."""
    db: DbSession = request.state.db

    query = select(Contact).where(Contact.workspace_id == context.id)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        query = query.where(
            or_(Contact.name.ilike(needle), Contact.e164.like(re.sub(r"[\s\-()]", "", needle)))
        )

    rows = (
        (
            await db.execute(
                query.order_by(func.lower(Contact.name), Contact.id)
                .offset(offset)
                .limit(PAGE + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > PAGE
    rows = rows[:PAGE]

    # One query for the whole page: the newest conversation per number, matched on
    # the identity the channel stored.
    numbers = [row.e164 for row in rows]
    heard: dict[str, str] = {}
    if numbers:
        latest = await db.execute(
            select(Conversation.external_id, func.max(Conversation.started_at))
            .where(
                Conversation.workspace_id == context.id,
                Conversation.external_id.in_(numbers),
            )
            .group_by(Conversation.external_id)
        )
        heard = {number: when.isoformat() for number, when in latest if number is not None}

    answers = []
    for row in rows:
        answer = _out(row)
        answer.last_heard_at = heard.get(row.e164)
        answers.append(answer)
    return ContactPage(contacts=answers, has_more=has_more)


class NewContact(BaseModel):
    e164: str = Field(min_length=4, max_length=25)
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.post(
    "", response_model=ContactOut, status_code=status.HTTP_201_CREATED, summary="Add a contact"
)
async def add_contact(
    request: Request,
    context: Annotated[WorkspaceContext, require_reception],
    payload: NewContact,
) -> object:
    db: DbSession = request.state.db

    e164 = _normalised(payload.e164)
    if e164 is None:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_e164",
            message="A number is written as +, country code, digits - like +43664123456.",
        )
    name = payload.name.strip()
    if not name:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_name",
            message="Give the contact a name.",
        )

    clash = await db.scalar(
        select(Contact).where(Contact.workspace_id == context.id, Contact.e164 == e164)
    )
    if clash is not None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="contact_exists",
            message="A contact with this number already exists.",
        )

    row = Contact(
        workspace_id=context.id,
        e164=e164,
        name=name,
        tags=_cleaned_tags(payload.tags) or None,
        notes=payload.notes.strip() if payload.notes else None,
    )
    db.add(row)
    await db.flush()
    answer = _out(row)
    await db.commit()
    logger.info("contact added", extra={"workspace_id": context.id, "contact_id": answer.id})
    return answer


class ContactChange(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.patch("/{contact_id}", response_model=ContactOut, summary="Edit a contact")
async def change_contact(
    request: Request,
    context: Annotated[WorkspaceContext, require_reception],
    contact_id: int,
    payload: ContactChange,
) -> object:
    """The name, the tags, the note - not the number. A different number is a
    different contact; editing it in place would quietly re-address the history
    this row is matched against."""
    db: DbSession = request.state.db

    row = await db.scalar(
        select(Contact).where(Contact.workspace_id == context.id, Contact.id == contact_id)
    )
    if row is None:
        return _missing()

    name = payload.name.strip()
    if not name:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_name",
            message="Give the contact a name.",
        )

    row.name = name
    row.tags = _cleaned_tags(payload.tags) or None
    row.notes = payload.notes.strip() if payload.notes else None
    answer = _out(row)
    await db.commit()
    logger.info("contact changed", extra={"workspace_id": context.id, "contact_id": contact_id})
    return answer


@router.delete("/{contact_id}", summary="Remove a contact")
async def remove_contact(
    request: Request,
    context: Annotated[WorkspaceContext, require_reception],
    contact_id: int,
) -> object:
    """Removes the name, not the history: conversations keep their rows, and
    `contact_id` on them resets to null by the constraint this table arrived with."""
    db: DbSession = request.state.db

    row = await db.scalar(
        select(Contact).where(Contact.workspace_id == context.id, Contact.id == contact_id)
    )
    if row is None:
        return _missing()

    await db.delete(row)
    await db.commit()
    logger.info("contact removed", extra={"workspace_id": context.id, "contact_id": contact_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
