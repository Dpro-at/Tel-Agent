"""Knowledge — the sources an assistant is allowed to read.

**A title and some text.** The screen also draws a crawler, a PDF parser and an index
rebuilding; each is its own subsystem and each lands on top of this, not instead of it.
What a piece of knowledge *is* does not change when a crawler puts it there.

**`assistant_id` is validated against this workspace, not just against the table.** A
foreign assistant's id would otherwise attach one customer's knowledge to another
customer's assistant through a column that has no idea which workspace it is in - the
foreign key alone does not carry the tenant.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api import webhooks
from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import CONTENT_MAX, Assistant, Knowledge
from api.security import audit
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.knowledge")

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


async def _told(db: DbSession, workspace_id: int, *, data: dict[str, object]) -> None:
    """Queue `knowledge.changed` for whoever registered for it, and commit.

    Swallowed on failure, like every export: the edit already stands, and undoing it
    because a webhook could not be written down would punish the operator for the
    receiver's problem. A subscriber typically re-syncs whatever mirrors the knowledge,
    so the payload carries which source and what happened, not the content.
    """
    try:
        await webhooks.queue(
            db, workspace_id=workspace_id, event="knowledge.changed", data=data
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("could not queue knowledge.changed", extra={"data": data})


class KnowledgeOut(BaseModel):
    id: int
    title: str
    content: str
    # Null means every assistant in the workspace.
    assistant_id: int | None
    assistant_name: str | None
    created_at: str
    updated_at: str


def _out(row: Knowledge, assistant_name: str | None) -> KnowledgeOut:
    return KnowledgeOut(
        id=row.id,
        title=row.title,
        content=row.content,
        assistant_id=row.assistant_id,
        assistant_name=assistant_name,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _missing() -> object:
    """A foreign id must be indistinguishable from one that does not exist."""
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such knowledge in this workspace.",
    )


async def _find(db: DbSession, workspace_id: int, knowledge_id: int) -> Knowledge | None:
    return await db.scalar(
        select(Knowledge).where(
            Knowledge.workspace_id == workspace_id, Knowledge.id == knowledge_id
        )
    )


async def _assistant_ok(db: DbSession, workspace_id: int, assistant_id: int | None) -> bool:
    """None is always fine - it means every assistant here."""
    if assistant_id is None:
        return True
    return (
        await db.scalar(
            select(Assistant).where(
                Assistant.workspace_id == workspace_id, Assistant.id == assistant_id
            )
        )
    ) is not None


def _no_such_assistant() -> object:
    return envelope_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="no_such_assistant",
        message="No such assistant in this workspace.",
    )


@router.get("", response_model=list[KnowledgeOut], summary="The knowledge in this workspace")
async def list_knowledge(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> list[KnowledgeOut]:
    db: DbSession = request.state.db
    # One join rather than a lookup per row: the assistant's name is on the screen
    # beside every source, and a workspace's knowledge is a list, not a page.
    rows = (
        await db.execute(
            select(Knowledge, Assistant.name)
            .outerjoin(Assistant, Assistant.id == Knowledge.assistant_id)
            .where(Knowledge.workspace_id == context.id)
            .order_by(Knowledge.created_at, Knowledge.id)
        )
    ).all()
    return [_out(row, name) for row, name in rows]


class NewKnowledge(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=CONTENT_MAX)
    assistant_id: int | None = None


@router.post(
    "",
    response_model=KnowledgeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a source",
)
async def add_knowledge(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewKnowledge,
) -> object:
    db: DbSession = request.state.db

    title = payload.title.strip()
    if not title:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_title",
            message="A source needs a title.",
        )
    if not await _assistant_ok(db, context.id, payload.assistant_id):
        return _no_such_assistant()

    row = Knowledge(
        workspace_id=context.id,
        assistant_id=payload.assistant_id,
        title=title,
        content=payload.content,
    )
    db.add(row)
    await db.flush()
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "knowledge_added",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"knowledge_id": row.id, "title": row.title},
    )
    logger.info("knowledge %s added in workspace %s", row.id, context.id)
    await _told(
        db, context.id, data={"knowledge_id": row.id, "title": row.title, "action": "added"}
    )
    # Scoped, like the write path above it. Nothing can reach here with an assistant
    # from another workspace today - `_assistant_ok` refuses that on the way in - and
    # this is the read that would quietly print the name if anything ever did.
    name = await db.scalar(
        select(Assistant.name).where(
            Assistant.id == row.assistant_id, Assistant.workspace_id == context.id
        )
    )
    return _out(row, name)


class EditKnowledge(BaseModel):
    """Every field optional: the screen edits a title without resending the text."""

    title: str | None = Field(default=None, min_length=1, max_length=160)
    content: str | None = Field(default=None, min_length=1, max_length=CONTENT_MAX)
    assistant_id: int | None = None


@router.patch("/{knowledge_id}", response_model=KnowledgeOut, summary="Edit a source")
async def edit_knowledge(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    knowledge_id: int,
    payload: EditKnowledge,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, knowledge_id)
    if row is None:
        return _missing()

    sent = payload.model_dump(exclude_unset=True)

    if "title" in sent:
        title = (sent["title"] or "").strip()
        if not title:
            return envelope_response(
                status_code=status.HTTP_400_BAD_REQUEST,
                code="invalid_title",
                message="A source needs a title.",
            )
        row.title = title
    if "content" in sent:
        row.content = sent["content"]
    # Sent as null on purpose is "every assistant", which is a real edit.
    if "assistant_id" in sent:
        if not await _assistant_ok(db, context.id, sent["assistant_id"]):
            return _no_such_assistant()
        row.assistant_id = sent["assistant_id"]

    row.updated_at = dt.datetime.now(dt.UTC)
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "knowledge_changed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"knowledge_id": row.id, "fields": sorted(sent)},
    )
    await _told(
        db, context.id, data={"knowledge_id": row.id, "title": row.title, "action": "changed"}
    )
    # Scoped, like the write path above it. Nothing can reach here with an assistant
    # from another workspace today - `_assistant_ok` refuses that on the way in - and
    # this is the read that would quietly print the name if anything ever did.
    name = await db.scalar(
        select(Assistant.name).where(
            Assistant.id == row.assistant_id, Assistant.workspace_id == context.id
        )
    )
    return _out(row, name)


@router.delete("/{knowledge_id}", summary="Remove a source")
async def remove_knowledge(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    knowledge_id: int,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, knowledge_id)
    if row is None:
        return _missing()

    title = row.title
    await db.delete(row)
    await db.commit()

    await audit.record(
        db,
        "knowledge_removed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"knowledge_id": knowledge_id, "title": title},
    )
    logger.info("knowledge %s removed from workspace %s", knowledge_id, context.id)
    await _told(
        db, context.id, data={"knowledge_id": knowledge_id, "title": title, "action": "removed"}
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
