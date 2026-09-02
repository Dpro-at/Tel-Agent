"""Routing rules — the three columns on the rules screen.

**The record; the engine is `api/routing.py` since Milestone 4.** Which rules
exist is the dashboard's business; applying them is the engine's, and the text
channels call it on every arriving contact. Still no `hits` counter and no
execution order column: matching order (exact before prefix, longer before
shorter) is the engine's own, and a counter is a feature nobody has asked for.

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
from sqlalchemy import func, select
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
    pattern: str
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
        pattern=row.pattern,
        action=row.action,
        note=row.note,
        created_at=row.created_at.isoformat(),
    )


def _normalise(raw: str) -> str | None:
    """The pattern as stored.

    Two families since Milestone 4. A phone shape - `+` and digits, optionally ending
    in `*` - keeps #89's cleaning, so `+43 1 402-8811` stores as one number. Anything
    else is a channel identity - an email address, a Telegram @username, a Slack or
    Discord user id - stored lowercased (matching is case-insensitive, and showing an
    operator a casing the matcher ignores would be a small lie) with the trailing `*`
    still meaning prefix. Whitespace inside an identity is refused rather than
    stripped: no channel hands out identities with spaces, and silently repairing a
    paste hides the mistake it carries.
    """
    phone_shaped = re.sub(r"[\s\-()]", "", raw)
    if _E164.fullmatch(phone_shaped) or _PREFIX.fullmatch(phone_shaped):
        return phone_shaped
    identity = raw.strip().lower()
    if len(identity) < 2 or len(identity) > 320 or re.search(r"\s", identity):
        return None
    # `*` only as a trailing prefix marker, with a real stem before it: a star in
    # the middle promises glob matching the engine does not do, and a bare `*` -
    # "match everyone" - is a policy, not a rule about somebody.
    if "*" in identity[:-1] or identity == "*":
        return None
    return identity


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
    """The most recent contact matching `pattern`, on any channel.

    Two lookups, latest wins: a phone call by caller number (`calls.from_e164`), and
    any conversation by its `external_id` - which is the same identity the rules
    engine matches on, so what this shows is what the rule actually governs.
    """

    def _matching(query, column):  # noqa: ANN001, ANN202 - two ORM shapes, one filter
        if pattern.endswith("*"):
            return query.where(func.lower(column).like(pattern[:-1].lower() + "%"))
        return query.where(func.lower(column) == pattern.lower())

    by_call = _matching(
        select(Conversation.started_at, Conversation.handling)
        .join(Call, Call.conversation_id == Conversation.id)
        .where(Conversation.workspace_id == workspace_id),
        Call.from_e164,
    )
    by_identity = _matching(
        select(Conversation.started_at, Conversation.handling).where(
            Conversation.workspace_id == workspace_id
        ),
        Conversation.external_id,
    )

    latest = None
    for query in (by_call, by_identity):
        row = (
            await db.execute(query.order_by(Conversation.started_at.desc()).limit(1))
        ).first()
        if row is not None and (latest is None or row[0] > latest[0]):
            latest = row
    if latest is None:
        return None
    started_at, handling = latest
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
        called = await _last_call(db, context.id, row.pattern)
        if called is not None:
            answer.last_called_at, answer.last_handling = called
        answers.append(answer)
    return answers


class NewRule(BaseModel):
    pattern: str = Field(min_length=2, max_length=320)
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
            message="A rule matches a full number like +43664123456, an identity "
            "like boss@example.com or @username, or a prefix ending in * like "
            "+43720*. No spaces.",
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

    pattern, refused = _checked(payload.action, payload.pattern)
    if pattern is None:
        return refused

    clash = await db.scalar(
        select(Rule).where(Rule.workspace_id == context.id, Rule.pattern == pattern)
    )
    if clash is not None:
        return envelope_response(
            status_code=status.HTTP_409_CONFLICT,
            code="rule_exists",
            message="A rule for this identity already exists. Move it instead of "
            "adding a second one.",
        )

    note = payload.note.strip() if payload.note else None
    row = Rule(workspace_id=context.id, pattern=pattern, action=payload.action, note=note)
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
            "pattern": answer.pattern,
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

    pattern = row.pattern
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
