"""Assistants — the list screen and the editor's two text panels.

**What is here is what an assistant owns.** §A6.6's editor has ten panels, and eight
of them read another subsystem: knowledge sources, tools, webhooks, email, sms,
booking, contacts, and forwarding. Each will be wired by the milestone that builds it,
against its own table. Wiring them now would mean this module inventing eight shapes
that the real features then have to live with.

**Deleting is real deleting.** An assistant is configuration, not a record of
something that happened - unlike a conversation, nothing about it is evidence, and a
soft-deleted row would only show up as a name that cannot be reused. Conversations it
answered are not touched: they carry their own transcript and stand without it.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import ASSISTANT_STATUSES, ASSISTANT_TEMPLATES, Assistant
from api.security import audit
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.assistants")

router = APIRouter(prefix="/api/assistants", tags=["assistants"])

# Long enough for the prompt §A6.6 shows and several times over, short enough that a
# paste of somebody's entire handbook is refused here rather than at the model, where
# it would cost money to find out.
_TEXT_MAX = 8000


class AssistantOut(BaseModel):
    id: int
    name: str
    role: str | None
    template: str
    status: str
    persona: str
    instructions: str
    language: str | None
    model: str | None
    created_at: str
    updated_at: str


def _out(row: Assistant) -> AssistantOut:
    return AssistantOut(
        id=row.id,
        name=row.name,
        role=row.role,
        template=row.template,
        status=row.status,
        persona=row.persona,
        instructions=row.instructions,
        language=row.language,
        model=row.model,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _missing() -> object:
    """A foreign id must be indistinguishable from one that does not exist."""
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such assistant in this workspace.",
    )


def _checked(template: str, assistant_status: str) -> object | None:
    """The two enumerated fields, validated once for create and for edit."""
    if template not in ASSISTANT_TEMPLATES:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_template",
            message=f"An assistant starts from one of {list(ASSISTANT_TEMPLATES)}.",
        )
    if assistant_status not in ASSISTANT_STATUSES:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_status",
            message=f"An assistant is one of {list(ASSISTANT_STATUSES)}.",
        )
    return None


async def _find(db: DbSession, workspace_id: int, assistant_id: int) -> Assistant | None:
    return await db.scalar(
        select(Assistant).where(
            Assistant.workspace_id == workspace_id, Assistant.id == assistant_id
        )
    )


async def _name_taken(
    db: DbSession, workspace_id: int, name: str, *, excluding: int | None = None
) -> bool:
    query = select(Assistant).where(
        Assistant.workspace_id == workspace_id, Assistant.name == name
    )
    if excluding is not None:
        query = query.where(Assistant.id != excluding)
    return await db.scalar(query) is not None


def _taken(name: str) -> object:
    return envelope_response(
        status_code=status.HTTP_409_CONFLICT,
        code="name_taken",
        message=f"An assistant called {name!r} already exists here.",
    )


@router.get("", response_model=list[AssistantOut], summary="The assistants in this workspace")
async def list_assistants(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> list[AssistantOut]:
    db: DbSession = request.state.db
    rows = (
        (
            await db.execute(
                select(Assistant)
                .where(Assistant.workspace_id == context.id)
                .order_by(Assistant.created_at, Assistant.id)
            )
        )
        .scalars()
        .all()
    )
    return [_out(row) for row in rows]


@router.get("/{assistant_id}", response_model=AssistantOut, summary="One assistant")
async def get_assistant(
    request: Request,
    context: Annotated[WorkspaceContext, require_viewer],
    assistant_id: int,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, assistant_id)
    if row is None:
        return _missing()
    return _out(row)


class NewAssistant(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=160)
    template: str = "blank"
    persona: str = Field(default="", max_length=_TEXT_MAX)
    instructions: str = Field(default="", max_length=_TEXT_MAX)
    language: str | None = Field(default=None, max_length=12)
    model: str | None = Field(default=None, max_length=80)


@router.post(
    "",
    response_model=AssistantOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add an assistant",
)
async def add_assistant(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewAssistant,
) -> object:
    db: DbSession = request.state.db

    refused = _checked(payload.template, "active")
    if refused is not None:
        return refused

    name = payload.name.strip()
    if not name:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_name",
            message="An assistant needs a name.",
        )
    if await _name_taken(db, context.id, name):
        return _taken(name)

    row = Assistant(
        workspace_id=context.id,
        name=name,
        role=payload.role,
        template=payload.template,
        status="active",
        persona=payload.persona,
        instructions=payload.instructions,
        language=payload.language,
        model=payload.model,
    )
    db.add(row)
    await db.flush()
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "assistant_added",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"assistant_id": row.id, "name": row.name},
    )
    logger.info("assistant %s created in workspace %s", row.id, context.id)
    return _out(row)


class EditAssistant(BaseModel):
    """Every field optional: the editor saves one panel at a time, not the whole form."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=160)
    template: str | None = None
    status: str | None = None
    persona: str | None = Field(default=None, max_length=_TEXT_MAX)
    instructions: str | None = Field(default=None, max_length=_TEXT_MAX)
    language: str | None = Field(default=None, max_length=12)
    model: str | None = Field(default=None, max_length=80)


@router.patch("/{assistant_id}", response_model=AssistantOut, summary="Edit an assistant")
async def edit_assistant(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    assistant_id: int,
    payload: EditAssistant,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, assistant_id)
    if row is None:
        return _missing()

    refused = _checked(payload.template or row.template, payload.status or row.status)
    if refused is not None:
        return refused

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_name",
                message="An assistant needs a name.",
            )
        if await _name_taken(db, context.id, name, excluding=row.id):
            return _taken(name)
        row.name = name

    # `exclude_unset` rather than a null check: clearing `role` back to nothing is a
    # real edit, and a payload that never mentioned it is not.
    sent = payload.model_dump(exclude_unset=True)
    for field in ("role", "template", "status", "persona", "instructions", "language", "model"):
        if field in sent:
            setattr(row, field, sent[field])

    row.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "assistant_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"assistant_id": row.id, "fields": sorted(sent)},
    )
    return _out(row)


@router.delete("/{assistant_id}", summary="Delete an assistant")
async def delete_assistant(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    assistant_id: int,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, assistant_id)
    if row is None:
        return _missing()

    name = row.name
    await db.delete(row)
    await db.commit()

    await audit.record(
        db,
        "assistant_removed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"assistant_id": assistant_id, "name": name},
    )
    logger.info("assistant %s deleted from workspace %s", assistant_id, context.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
