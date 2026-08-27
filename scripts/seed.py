"""Development seed data — one workspace, and a person in every role.

**Never run this against an installation anybody depends on.** It creates accounts with
published passwords, and it refuses to run unless `ENVIRONMENT=development`.

Twenty-nine screens render from fixtures today. Replacing a fixture with an empty
database makes every screen look broken and hides real regressions, so the point of
this script is a database the dashboard can actually be developed against — including
one account per role, so that authorisation can be exercised rather than assumed.

    python scripts/seed.py            # create, refusing to touch an existing database
    python scripts/seed.py --reset    # drop everything first

Every account uses the same password, printed at the end. It is long enough to satisfy
the minimum and obvious enough that nobody could mistake it for a real one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.db import create_engine, create_sessionmaker, session_scope
from api.models import (
    App,
    AppInstall,
    Call,
    Channel,
    Conversation,
    Membership,
    Message,
    Number,
    User,
    Workspace,
)
from api.security.password import hash_password

# Obvious on sight, and long enough to pass the minimum length rule.
SEED_PASSWORD = "development-only-password"  # noqa: S105

WORKSPACE_NAME = "Wagner & Partner"
SECOND_WORKSPACE_NAME = "Wolf Studio"

# One account per role, so every permission path has somebody to test it with.
PEOPLE = [
    ("wagner", "wagner@example.test", "owner", "Leonhard Wagner"),
    ("mohamed", "mohamed@example.test", "admin", "Mohamed Tofeek"),
    ("sabine", "sabine@example.test", "reception", "Sabine Wolf"),
    ("lukas", "lukas@example.test", "viewer", "Lukas Berger"),
    ("julia", "julia@example.test", "invited", "Julia Hofer"),
]


async def _wipe(session: AsyncSession) -> None:
    """Delete everything, children first."""
    for model in (
        Message,
        Call,
        Conversation,
        Number,
        Channel,
        AppInstall,
        App,
        Membership,
        User,
        Workspace,
    ):
        await session.execute(delete(model))
    await session.commit()


async def seed(reset: bool) -> None:
    settings = get_settings()

    # The guard, and it is not advisory. These accounts have a password that is written
    # down in a public repository.
    if settings.environment != "development":
        raise SystemExit(
            "Refusing to seed: ENVIRONMENT is "
            f"{settings.environment!r}, not 'development'. These accounts share one "
            "published password and must never exist on an installation anybody uses."
        )

    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)

    try:
        async with session_scope(sessionmaker) as session:
            if reset:
                await _wipe(session)

            existing = await session.scalar(select(User).limit(1))
            if existing is not None:
                raise SystemExit(
                    "This database already has accounts. Re-run with --reset to "
                    "replace them, or point DATABASE_URL somewhere else."
                )

            password_hash = hash_password(SEED_PASSWORD)

            workspace = Workspace(name=WORKSPACE_NAME)
            # A second workspace, so the switcher has something to switch to and
            # cross-workspace isolation can actually be tested rather than asserted.
            second = Workspace(name=SECOND_WORKSPACE_NAME)
            session.add_all([workspace, second])
            await session.flush()

            users: dict[str, User] = {}
            for username, email, role, _display in PEOPLE:
                user = User(username=username, email=email, password_hash=password_hash)
                session.add(user)
                await session.flush()
                users[role] = user
                session.add(Membership(user_id=user.id, workspace_id=workspace.id, role=role))

            # The owner is also the owner of the second workspace; nobody else is a
            # member of it. That asymmetry is the point: it is what a leaking query
            # would expose.
            session.add(
                Membership(user_id=users["owner"].id, workspace_id=second.id, role="owner")
            )

            web_chat = App(slug="web_chat", origin="official", version="1.6.3")
            session.add(web_chat)
            await session.flush()
            session.add(AppInstall(workspace_id=workspace.id, app_id=web_chat.id))

            channel = Channel(
                workspace_id=workspace.id,
                kind="web",
                name="Web chat",
                app_id=web_chat.id,
            )
            session.add(Channel(workspace_id=second.id, kind="web", name="Web chat"))
            session.add(channel)
            await session.flush()

            conversation = Conversation(
                workspace_id=workspace.id,
                channel_id=channel.id,
                direction="inbound",
                summary="Asked for an appointment next week.",
                intent="appointment",
                handling="ai",
            )
            session.add(conversation)
            await session.flush()

            lines = [
                ("caller", "Guten Tag, ich haette gerne einen Termin naechste Woche."),
                ("agent", "Sehr gerne. Welcher Tag passt Ihnen am besten?"),
                ("caller", "Am liebsten Dienstag Vormittag."),
                ("agent", "Dienstag um zehn Uhr ist frei. Soll ich das eintragen?"),
            ]
            for index, (speaker, text) in enumerate(lines):
                session.add(
                    Message(
                        workspace_id=workspace.id,
                        conversation_id=conversation.id,
                        ts_ms=index * 4200,
                        speaker=speaker,
                        text=text,
                    )
                )

            await session.commit()
    finally:
        await engine.dispose()

    print(f"Seeded {WORKSPACE_NAME!r} and {SECOND_WORKSPACE_NAME!r}.\n")
    print(f"{'username':<12} {'role':<12} email")
    for username, email, role, _display in PEOPLE:
        print(f"{username:<12} {role:<12} {email}")
    print(f"\nPassword for every account: {SEED_PASSWORD}")
    print("Development only. Delete this database before anybody relies on it.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="delete existing rows before seeding"
    )
    asyncio.run(seed(parser.parse_args().reset))
