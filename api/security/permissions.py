"""Who may do what, in one place — the enforcement the five roles have been waiting for.

The seed script creates one account per role precisely so authorisation can be
exercised rather than assumed; until now there was nothing to exercise. Every check a
route needs is built from two dependencies here, so a permission decision is never
hand-rolled at a call site — a check written twice is a check that will differ, and a
check forgotten is a leak (D-028's words: one customer's transcripts on another
customer's screen).

**The model.** A person's power exists only *inside a workspace*, through their
membership row. The roles order strictly:

    owner > admin > reception > viewer        invited: no access at all

`invited` is a membership state, not a rank: the interface lists invited people in the
same table with a "Cancel invite" action, but until they accept they can read nothing.
Fine-grained capabilities (the settings screen's role matrix) are expressed as
functions of the role here, so when a capability stops mapping cleanly onto the
ordering it gets its own rule in this file rather than a special case in a route.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.dependencies import CurrentUser
from api.models import Membership, Workspace

# Higher outranks lower. `invited` is deliberately absent - having no rank is the
# mechanism by which an invitation grants nothing until it is accepted.
_RANK = {"viewer": 1, "reception": 2, "admin": 3, "owner": 4}


class WorkspaceContext:
    """The workspace a request is acting in, and the caller's role there."""

    def __init__(self, workspace: Workspace, role: str) -> None:
        self.workspace = workspace
        self.role = role

    @property
    def id(self) -> int:
        return self.workspace.id

    def outranks_or_is(self, minimum: str) -> bool:
        return _RANK.get(self.role, 0) >= _RANK[minimum]


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message)


async def get_workspace_context(
    request: Request,
    user: CurrentUser,
    x_workspace_id: Annotated[
        int | None,
        Header(
            description="The workspace this request acts in. Omitted, the user's "
            "first workspace is assumed - the common case of belonging to one."
        ),
    ] = None,
) -> WorkspaceContext:
    """Resolve the acting workspace and the caller's role in it.

    Membership is checked here and nowhere else. A workspace id the caller does not
    belong to answers exactly like one that does not exist — 403 either way — because
    "that workspace exists but is not yours" tells an attacker which ids are real.
    """
    db: DbSession = request.state.db

    query = (
        select(Membership, Workspace)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(Membership.user_id == user.id)
        .order_by(Membership.workspace_id)
    )
    if x_workspace_id is not None:
        query = query.where(Membership.workspace_id == x_workspace_id)

    row = (await db.execute(query.limit(1))).first()
    if row is None:
        raise _forbidden("You are not a member of this workspace.")

    membership, workspace = row
    if membership.role == "invited":
        # An invitation is a pending fact, not a key. Accepting it is what turns the
        # row into access, and that flow arrives with the workspaces epic.
        raise _forbidden("This invitation has not been accepted yet.")

    return WorkspaceContext(workspace, membership.role)


CurrentWorkspace = Annotated[WorkspaceContext, Depends(get_workspace_context)]


def require_role(minimum: str) -> Any:
    """A dependency refusing callers below `minimum` in the acting workspace.

        @router.post("/api/rules")
        async def create_rule(context: Annotated[WorkspaceContext, require_admin]): ...

    The refusal is 403 with the required role named: the person is signed in and
    allowed to know why the door did not open — unlike the membership check above,
    where naming the reason would leak which workspaces exist.
    """
    if minimum not in _RANK:
        raise ValueError(f"unknown role {minimum!r} - one of {sorted(_RANK)}")

    async def dependency(context: CurrentWorkspace) -> WorkspaceContext:
        if not context.outranks_or_is(minimum):
            raise _forbidden(f"This needs the {minimum} role or above.")
        return context

    return Depends(dependency)


# The spellings routes actually use, so a typo is an import error rather than a
# runtime surprise on the one request that mattered.
require_viewer = require_role("viewer")
require_reception = require_role("reception")
require_admin = require_role("admin")
require_owner = require_role("owner")
