"""Development seeding keeps credentials out of terminal and captured log output."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from api.db import create_engine, create_sessionmaker, session_scope
from api.models import User
from api.security.password import verify_password
from scripts import seed as seed_script


async def test_seed_creates_usable_accounts_without_printing_password(
    migrated, database_url, monkeypatch, capsys, caplog
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", database_url)
    password = secrets.token_urlsafe(24)
    monkeypatch.setattr(seed_script, "SEED_PASSWORD", password)

    await seed_script.seed(reset=False)

    captured = capsys.readouterr()
    assert password not in captured.out + captured.err + caplog.text
    assert "scripts/seed.py" in captured.out
    assert "Development only" in captured.out

    engine = create_engine(seed_script.get_settings())
    try:
        async with session_scope(create_sessionmaker(engine)) as session:
            users = (await session.scalars(select(User))).all()
            assert len(users) == len(seed_script.PEOPLE)
            for user in users:
                assert user.username in captured.out
                assert verify_password(password, user.password_hash)
    finally:
        await engine.dispose()


async def test_seed_refuses_production_before_opening_database(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENCRYPTION_KEY", secrets.token_hex(32))

    def unexpected_database_access(*args, **kwargs):
        pytest.fail("Production seeding must refuse before opening the database")

    monkeypatch.setattr(seed_script, "create_engine", unexpected_database_access)
    with pytest.raises(SystemExit, match="Refusing to seed"):
        await seed_script.seed(reset=True)

    captured = capsys.readouterr()
    assert seed_script.SEED_PASSWORD not in captured.out + captured.err
