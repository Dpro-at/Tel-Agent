"""Routing rules — the three columns on the rules screen.

**The record, not the engine.** Which rules exist is the dashboard's business;
applying them to a live call is the agent's, at Milestone 11. So nothing here has
a `hits` counter or an execution order — neither is a fact until something matches
calls against rules, and drawing them before then would be the screen inventing
data.

**"When did this number last call" is real, though**, and §A6.5 asks for it: rule
and consequence in one view. It is answered from the archive — the latest phone
conversation whose caller matches the rule's pattern — so the screen can show that
a blocked number has in fact stopped calling, or that it has not.
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
from api.models import RULE_ACTIONS, Call, Conversation, Rule
from api.security import audit
from api.security.permissions import WorkspaceContext, require_admin, require_viewer

logger = logging.getLogger("api.rules")

router = APIRouter(prefix="/api/rules", tags=["rules"])

# An exact E.164, or an E.164 prefix (at least a country code) ending in `*`.
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_PREFIX = re.compile(r"^\+[1-9]\d{1,13}\*$")


class RuleOut(BaseModel):
    id: int
    e164_or_pattern: str
    action: str
    note: str | None
    created_at: str
    # The latest phone call from a matching number, from the archive. Null when no
    # such call is stored - which for a blocked number is the good outcome.
    last_called_at: str | None = None
    last_handling: str | None = None


def _out(row: Rule) -> RuleOut:
    return RuleOut(
        id=row.id,
        e164_or_pattern=row.e164_or_pattern,
        action=row.action,
        note=row.note,
        created_at=row.created_at.isoformat(),
    )


def _normalise(raw: str) -> str | None:
    """The pattern as stored: digits and `+`, a trailing `*` kept if present."""
    cleaned = re.sub(r"[\s\-()]", "", raw)
    if _E164.fullmatch(cleaned) or _PREFIX.fullmatch(cleaned):
        return cleaned
    return None


def _missing() -> object:
    """A foreign id must be indistinguishable from one that does not exist."""
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such rule in this workspace.",
    )


async def _last_call(
    db: DbSession, workspace_id: int, pattern: str
) -> tuple[str, str | None] | None:
    """The most recent phone conversation whose caller matches `pattern`."""
    query = (
        select(Conversation.started_at, Conversation.handling)
        .join(Call, Call.conversation_id == Conversation.id)
        .where(Conversation.workspace_id == workspace_id)
    )
    if pattern.endswith("*"):
        query = query.where(Call.from_e164.like(pattern[:-1] + "%"))
    else:
        query = query.where(Call.from_e164 == pattern)
    row = (await db.execute(query.order_by(Conversation.started_at.desc()).limit(1))).first()
    if row is None:
        return None
    started_at, handling = row
    return started_at.isoformat(), handling


@router.get("", response_model=list[RuleOut], summary="The rules in this workspace")
async def list_rules(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> list[RuleOut]:
    db: DbSession = request.state.db
    rows = (
        (
            await db.execute(
                select(Rule)
                .where(Rule.workspace_id == context.id)
                .order_by(Rule.created_at, Rule.id)
            )
        )
        .scalars()
        .all()
    )
    # One archive lookup per rule. A workspace's rules are dozens at most, and each
    # lookup is an indexed point (or prefix) query - not worth a join nobody can read.
    answers = []
    for row in rows:
        answer = _out(row)
        called = await _last_call(db, context.id, row.e164_or_pattern)
        if called is not None:
            answer.last_called_at, answer.last_handling = called
        answers.append(answer)
    return answers


class NewRule(BaseModel):
    e164_or_pattern: str = Field(min_length=2, max_length=25)
    action: str
    note: str | None = Field(default=None, max_length=200)


def _checked(payload_action: str, raw_pattern: str) -> tuple[str, None] | tuple[None, object]:
    """Validate the two typed fields once, for create and for edit."""
    if payload_action not in RULE_ACTIONS:
        return None, envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_action",
            message=f"A rule does one of {list(RULE_ACTIONS)}.",
        )
    pattern = _normalise(raw_pattern)
    if pattern is None:
        return None, envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_pattern",
            message="A rule matches a full number like +43664123456, or a prefix "
            "ending in * like +43720*.",
        )
    return pattern, None


@router.post(
    "", response_model=RuleOut, status_code=status.HTTP_201_CREATED, summary="Add a rule"
)
async def add_rule(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewRule,
) -> object:
    db: DbSession = request.state.db

    pattern, refused = _checked(payload.action, payload.e164_or_pattern)
    if pattern is None:
        return refused

    clash = await db.scalar(
        select(Rule).where(Rule.workspace_id == context.id, Rule.e164_or_pattern == pattern)
    )
    if clash is not None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="rule_exists",
            message="A rule for this number already exists. Move it instead of "
            "adding a second one.",
        )

    note = payload.note.strip() if payload.note else None
    row = Rule(
        workspace_id=context.id, e164_or_pattern=pattern, action=payload.action, note=note
    )
    db.add(row)
    await db.flush()
    answer = _out(row)
    acting_id, acting_name = user.id, user.username
    await db.commit()
    await audit.record(
        db,
        "rule_added",
        request=request,
        user_id=acting_id,
        username=acting_name,
        details={"workspace_id": context.id, "pattern": pattern, "action": payload.action},
    )
    logger.info("rule added", extra={"workspace_id": context.id, "rule_id": answer.id})
    return answer


class RuleChange(BaseModel):
    action: str
    note: str | None = Field(default=None, max_length=200)


@router.patch("/{rule_id}", response_model=RuleOut, summary="Move a rule to another column")
async def change_rule(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    rule_id: int,
    payload: RuleChange,
) -> object:
    db: DbSession = request.state.db

    if payload.action not in RULE_ACTIONS:
        return envelope_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="invalid_action",
            message=f"A rule does one of {list(RULE_ACTIONS)}.",
        )

    row = await db.scalar(
        select(Rule).where(Rule.workspace_id == context.id, Rule.id == rule_id)
    )
    if row is None:
        return _missing()

    was = row.action
    row.action = payload.action
    row.note = payload.note.strip() if payload.note else None
    answer = _out(row)
    acting_id, acting_name = user.id, user.username
    await db.commit()
    await audit.record(
        db,
        "rule_changed",
        request=request,
        user_id=acting_id,
        username=acting_name,
        details={
            "workspace_id": context.id,
            "pattern": answer.e164_or_pattern,
            "from": was,
            "to": answer.action,
        },
    )
    logger.info(
        "rule changed",
        extra={"workspace_id": context.id, "rule_id": rule_id, "action": answer.action},
    )
    return answer


@router.delete("/{rule_id}", summary="Remove a rule")
async def remove_rule(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    rule_id: int,
) -> object:
    db: DbSession = request.state.db

    row = await db.scalar(
        select(Rule).where(Rule.workspace_id == context.id, Rule.id == rule_id)
    )
    if row is None:
        return _missing()

    pattern = row.e164_or_pattern
    acting_id, acting_name = user.id, user.username
    await db.delete(row)
    await db.commit()
    await audit.record(
        db,
        "rule_removed",
        request=request,
        user_id=acting_id,
        username=acting_name,
        details={"workspace_id": context.id, "pattern": pattern},
    )
    logger.info("rule removed", extra={"workspace_id": context.id, "rule_id": rule_id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
