"""The machine tokens, and the one moment each of them is visible.

The registry side of §B9.1: an admin mints a credential for a machine path, sees it
once, and can rotate or remove it afterwards. It is deliberately the same shape as
`api/routes/webhooks.py` — shown in full in the response that creates it and never
again — because a credential that can be re-read is a credential every later database
dump leaks, and one the operator chooses is a chosen password.

**Rotation keeps the row.** §B9.1 asks for a rotatable credential, and rotating in
place is what makes that usable: the name, the scope and the record of when it was
last used all survive, so an operator rotating a suspected leak does not also lose the
evidence of what it had been doing.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.errors import envelope_response
from api.models import MACHINE_SCOPES, MachineToken
from api.security import audit
from api.security.machine_tokens import mint
from api.security.permissions import WorkspaceContext, require_admin, require_viewer
from api.security.session import hash_token

logger = logging.getLogger("api.tokens")

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenOut(BaseModel):
    id: int
    name: str
    scope: str
    # The four characters an operator recognises in a list of six. Stored beside the
    # hash rather than derived from it, because a hash cannot be read back.
    last_four: str
    created_at: str
    last_used_at: str | None


class TokenCreated(TokenOut):
    """The mint and rotate responses, which carry the token once and never again."""

    token: str


def _out(row: MachineToken) -> TokenOut:
    return TokenOut(
        id=row.id,
        name=row.name,
        scope=row.scope,
        last_four=row.last_four,
        created_at=row.created_at.isoformat(),
        last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
    )


def _missing() -> Response:
    return envelope_response(
        status_code=status.HTTP_404_NOT_FOUND,
        code="not_found",
        message="No such token in this workspace.",
    )


def _unknown_scope(scope: str) -> Response:
    return envelope_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="unknown_scope",
        message=f"Not a path this product guards: {scope}. "
        f"One of: {', '.join(MACHINE_SCOPES)}.",
    )


async def _find(db: DbSession, workspace_id: int, token_id: int) -> MachineToken | None:
    return await db.scalar(
        select(MachineToken).where(
            MachineToken.workspace_id == workspace_id, MachineToken.id == token_id
        )
    )


@router.get("/scopes", response_model=list[str], summary="The paths a token can be minted for")
async def list_scopes(context: Annotated[WorkspaceContext, require_viewer]) -> list[str]:
    """The vocabulary, so the screen does not carry a second copy of it."""
    return list(MACHINE_SCOPES)


@router.get("", response_model=list[TokenOut], summary="The machine tokens in this workspace")
async def list_tokens(
    request: Request, context: Annotated[WorkspaceContext, require_viewer]
) -> list[TokenOut]:
    db: DbSession = request.state.db
    rows = (
        (
            await db.execute(
                select(MachineToken)
                .where(MachineToken.workspace_id == context.id)
                .order_by(MachineToken.created_at, MachineToken.id)
            )
        )
        .scalars()
        .all()
    )
    return [_out(row) for row in rows]


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scope: str


@router.post(
    "",
    response_model=TokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Mint a token, and see it once",
)
async def add_token(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    payload: NewToken,
) -> object:
    db: DbSession = request.state.db
    if payload.scope not in MACHINE_SCOPES:
        return _unknown_scope(payload.scope)

    token = mint(payload.scope)
    row = MachineToken(
        workspace_id=context.id,
        name=payload.name,
        scope=payload.scope,
        token_hash=hash_token(token),
        last_four=token[-4:],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "machine_token_added",
        request=request,
        user_id=user.id,
        username=user.username,
        # The name and the scope, never the token. An audit trail is read by more
        # people than the screen that minted it.
        details={"token_id": row.id, "name": row.name, "scope": row.scope},
    )
    logger.info("machine token %s minted for %s in workspace %s", row.id, row.scope, context.id)
    return TokenCreated(**_out(row).model_dump(), token=token)


@router.post(
    "/{token_id}/rotate",
    response_model=TokenCreated,
    summary="Replace the token, and see the new one once",
)
async def rotate_token(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    token_id: int,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, token_id)
    if row is None:
        return _missing()

    token = mint(row.scope)
    row.token_hash = hash_token(token)
    row.last_four = token[-4:]
    await db.commit()
    await db.refresh(row)

    await audit.record(
        db,
        "machine_token_rotated",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"token_id": row.id, "name": row.name, "scope": row.scope},
    )
    logger.info("machine token %s rotated in workspace %s", row.id, context.id)
    return TokenCreated(**_out(row).model_dump(), token=token)


@router.delete("/{token_id}", summary="Remove a token")
async def remove_token(
    request: Request,
    context: Annotated[WorkspaceContext, require_admin],
    user: CurrentUser,
    token_id: int,
) -> object:
    db: DbSession = request.state.db
    row = await _find(db, context.id, token_id)
    if row is None:
        return _missing()

    name, scope = row.name, row.scope
    await db.delete(row)
    await db.commit()

    await audit.record(
        db,
        "machine_token_removed",
        request=request,
        user_id=user.id,
        username=user.username,
        details={"token_id": token_id, "name": name, "scope": scope},
    )
    logger.info("machine token %s removed from workspace %s", token_id, context.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
