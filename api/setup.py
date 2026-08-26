"""First run: the one path that creates an account, and it runs exactly once.

§B9 requires a password on first run, with no default credentials. That is the whole
point of this module: a fresh database has no account, no workspace and no way in, and
this is the only code that changes that.

It also creates what D-028 makes unavoidable. An administrator with no workspace can
see nothing, because every table that holds data is scoped by one — so the workspace,
the owner membership and the `web` channel are created in the same transaction as the
user. That channel is the row §B5 decision 6 asks for; it lands here rather than in the
migration because a channel needs a workspace to belong to, and a migration has none.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import Channel, Membership, User, Workspace
from api.security.password import hash_password


class AlreadySetUp(RuntimeError):
    """Raised when setup is attempted on an installation that already has an account.

    Not a validation error. If this can be triggered twice, anybody who can reach the
    port can add themselves as an owner — so it is checked inside the transaction that
    does the writing, not before it.
    """


@dataclass(frozen=True)
class FirstRun:
    """What the first run produced."""

    user: User
    workspace: Workspace
    channel: Channel


async def is_set_up(session: AsyncSession) -> bool:
    """Has anybody been created yet?

    "Is there a user" rather than a flag in a settings table: a flag can disagree with
    reality, and the question this answers is exactly whether an account exists.
    """
    count = await session.scalar(select(func.count()).select_from(User))
    return bool(count)


async def create_first_administrator(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    workspace_name: str,
    email: str | None = None,
    locale: str = "en",
) -> FirstRun:
    """Create the administrator, their workspace, and the web channel.

    One transaction. A half-finished first run — a user with no workspace, or a
    workspace nobody owns — is a state the interface cannot render and the operator
    cannot repair without a database client.
    """
    if await is_set_up(session):
        raise AlreadySetUp("This installation already has an account.")

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        locale=locale,
    )
    workspace = Workspace(name=workspace_name)
    session.add_all([user, workspace])
    await session.flush()

    # `owner` is the role the interface describes as "One person. Cannot be removed."
    session.add(Membership(user_id=user.id, workspace_id=workspace.id, role="owner"))

    # §B5 decision 6: `channels` holds one row of kind `web` from the start, so that
    # Milestone 3 is a write rather than a redesign.
    channel = Channel(workspace_id=workspace.id, kind="web", name="Web chat")
    session.add(channel)

    await session.commit()
    return FirstRun(user=user, workspace=workspace, channel=channel)
