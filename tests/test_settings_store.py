"""The settings store — P3.

The rule this exists to satisfy is CLAUDE.md's: *"Never build a settings screen that
writes to `.env`."* So the tests are about the properties a screen depends on —
fallback, masking, and the mask never being saved over a live secret.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.main import create_app
from api.models import Membership, Setting, User, Workspace
from api.security.crypto import decrypt, load_key
from api.security.password import hash_password
from api.settings import store
from api.settings.registry import UnknownSetting

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
MAIL_PASSWORD = "the-real-smtp-password-value"  # noqa: S105


@pytest.fixture
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield load_key(KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()


@pytest.fixture
async def workspace(migrated: AsyncSession) -> int:
    row = Workspace(name="Wagner & Partner")
    migrated.add(row)
    await migrated.commit()
    return row.id


# --- The store ---------------------------------------------------------------


async def test_an_unset_setting_returns_its_declared_default(
    migrated: AsyncSession,
) -> None:
    assert await store.get(migrated, "smtp.port") == 587
    assert await store.get(migrated, "recording.announce") is True


async def test_an_unknown_key_is_refused_at_the_call_that_made_it(
    migrated: AsyncSession,
) -> None:
    """A store that accepts typos accumulates settings nobody reads."""
    with pytest.raises(UnknownSetting):
        await store.get(migrated, "smpt.host")  # transposed on purpose
    with pytest.raises(UnknownSetting):
        await store.set_value(migrated, "smpt.host", "mail.example")


async def test_a_stored_value_wins_over_the_default(migrated: AsyncSession) -> None:
    await store.set_value(migrated, "smtp.port", 2525)
    await migrated.commit()

    assert await store.get(migrated, "smtp.port") == 2525


async def test_types_survive_the_round_trip(migrated: AsyncSession) -> None:
    """Everything is stored as text; the declared kind is applied on the way out."""
    await store.set_value(migrated, "smtp.port", 2525)
    await store.set_value(migrated, "recording.announce", False)
    await migrated.commit()

    assert await store.get(migrated, "smtp.port") == 2525
    assert await store.get(migrated, "recording.announce") is False


async def test_a_workspace_overrides_the_installation_and_falls_back(
    migrated: AsyncSession, workspace: int
) -> None:
    """The 'use the default' state a settings screen needs to be real."""
    await store.set_value(migrated, "recording.announce", False)  # installation-wide
    await migrated.commit()
    assert await store.get(migrated, "recording.announce", workspace_id=workspace) is False

    await store.set_value(migrated, "recording.announce", True, workspace_id=workspace)
    await migrated.commit()
    assert await store.get(migrated, "recording.announce", workspace_id=workspace) is True
    # The installation value is untouched by the override.
    assert await store.get(migrated, "recording.announce") is False

    await store.clear(migrated, "recording.announce", workspace_id=workspace)
    await migrated.commit()
    assert await store.get(migrated, "recording.announce", workspace_id=workspace) is False


async def test_an_installation_setting_refuses_a_workspace_scope(
    migrated: AsyncSession, workspace: int
) -> None:
    """One mail server per machine; a per-workspace value would be a setting that
    silently does nothing."""
    with pytest.raises(ValueError):
        await store.set_value(migrated, "smtp.host", "mail.example", workspace_id=workspace)


# --- Secrets -----------------------------------------------------------------


async def test_a_secret_is_stored_encrypted(
    migrated: AsyncSession, configured_key: bytes
) -> None:
    await store.set_value(migrated, "smtp.password", MAIL_PASSWORD)
    await migrated.commit()

    raw = (
        await migrated.execute(
            text("SELECT secret_value, value FROM settings WHERE key = 'smtp.password'")
        )
    ).first()
    assert raw is not None
    secret_value, plain_value = raw
    # The plaintext column is left empty, and the stored bytes are our envelope.
    assert plain_value is None
    assert MAIL_PASSWORD not in secret_value
    assert decrypt(secret_value, configured_key) == MAIL_PASSWORD

    # And the store hands the plaintext back to code that asks for it.
    assert await store.get(migrated, "smtp.password") == MAIL_PASSWORD


async def test_the_screen_view_masks_secrets_and_shows_the_rest(
    migrated: AsyncSession, configured_key: bytes
) -> None:
    await store.set_value(migrated, "smtp.password", MAIL_PASSWORD)
    await store.set_value(migrated, "smtp.host", "mail.example")
    await migrated.commit()

    view = await store.all_for(migrated)

    assert view["smtp.host"] == "mail.example"
    assert view["smtp.password"] != MAIL_PASSWORD
    assert view["smtp.password"].endswith(MAIL_PASSWORD[-4:])


# --- Through the endpoints ---------------------------------------------------


@pytest.fixture
async def clients(
    migrated: AsyncSession, settings: Settings, database_url: str, configured_key: bytes
):
    """An admin and a viewer, both signed in, against an app that can encrypt.

    `configured_key` is not decoration: without it the app has no ENCRYPTION_KEY, the
    secret column refuses to write, and the mask tests below would pass for the wrong
    reason - on a secret that was never stored at all.
    """
    space = Workspace(name="Wagner & Partner")
    migrated.add(space)
    await migrated.flush()
    password_hash = hash_password(PASSWORD)
    for role in ("admin", "viewer"):
        user = User(username=role, password_hash=password_hash)
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=space.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    opened: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        for role in ("admin", "viewer"):
            client = AsyncClient(transport=transport, base_url="http://localhost")
            assert (
                await client.post(
                    "/api/auth/login", json={"username": role, "password": PASSWORD}
                )
            ).status_code == 200
            opened[role] = client
        try:
            yield opened
        finally:
            for client in opened.values():
                await client.aclose()


async def test_a_viewer_cannot_read_or_write_settings(clients) -> None:
    """Settings are an administrative surface: a read-only account has no business
    seeing the mail server, masked or not."""
    assert (await clients["viewer"].get("/api/settings")).status_code == 403
    assert (
        await clients["viewer"].patch("/api/settings", json={"values": {"smtp.port": 25}})
    ).status_code == 403


async def test_an_admin_writes_and_reads_back_masked(clients) -> None:
    admin = clients["admin"]

    written = await admin.patch(
        "/api/settings",
        json={"values": {"smtp.host": "mail.example", "smtp.password": MAIL_PASSWORD}},
    )
    assert written.status_code == 200

    entries = {row["key"]: row for row in (await admin.get("/api/settings")).json()}
    assert entries["smtp.host"]["value"] == "mail.example"
    assert entries["smtp.password"]["value"] != MAIL_PASSWORD
    assert entries["smtp.password"]["secret"] is True


async def test_saving_the_mask_back_does_not_overwrite_the_secret(
    clients, migrated: AsyncSession
) -> None:
    """E4's acceptance condition. The screen renders the mask in the field; a user who
    edits an unrelated tab and presses save must not replace their live credential
    with four bullets."""
    admin = clients["admin"]
    await admin.patch("/api/settings", json={"values": {"smtp.password": MAIL_PASSWORD}})

    entries = {row["key"]: row for row in (await admin.get("/api/settings")).json()}
    mask_as_rendered = entries["smtp.password"]["value"]

    response = await admin.patch(
        "/api/settings",
        json={"values": {"smtp.password": mask_as_rendered, "smtp.host": "changed.example"}},
    )

    assert response.status_code == 200
    assert response.json()["ignored_masked"] == ["smtp.password"]
    # The unrelated field did save, and the credential survived intact.
    migrated.expire_all()
    assert await store.get(migrated, "smtp.host") == "changed.example"
    assert await store.get(migrated, "smtp.password") == MAIL_PASSWORD


async def test_clearing_a_field_falls_back_to_the_default(
    clients, migrated: AsyncSession
) -> None:
    admin = clients["admin"]
    await admin.patch("/api/settings", json={"values": {"smtp.port": 2525}})
    migrated.expire_all()
    assert await store.get(migrated, "smtp.port") == 2525

    await admin.patch("/api/settings", json={"values": {"smtp.port": ""}})

    migrated.expire_all()
    # Back to the declared default, and no row left behind holding an empty string.
    assert await store.get(migrated, "smtp.port") == 587
    assert (await migrated.scalar(select(Setting).where(Setting.key == "smtp.port"))) is None


async def test_an_unknown_key_is_refused_by_the_endpoint(clients) -> None:
    response = await clients["admin"].patch(
        "/api/settings", json={"values": {"smpt.host": "mail.example"}}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_setting"


# --- The mail server actually moves out of .env ------------------------------


async def test_the_store_beats_the_environment_for_mail(
    migrated: AsyncSession, settings: Settings, configured_key: bytes
) -> None:
    """P3's whole point: `.env` is what an installer wrote once, the store is what the
    owner changed afterwards. A screen value that lost to a stale environment variable
    would be a setting that appears to save and does nothing."""
    from api import mail

    from_env = settings.model_copy(
        update={"smtp_host": "old.example", "smtp_from": "old@example.test"}
    )

    before = await mail.resolve(migrated, from_env)
    assert before.host == "old.example"

    await store.set_value(migrated, "smtp.host", "new.example")
    await store.set_value(migrated, "smtp.from", "new@example.test")
    await migrated.commit()

    after = await mail.resolve(migrated, from_env)
    assert after.host == "new.example"
    assert after.sender == "new@example.test"
    assert after.configured is True


# --- The two failures the browser found ---------------------------------------


async def test_the_column_a_secret_is_written_to_is_redacted() -> None:
    """A SQLAlchemy parameter dump carried a live password into the log.

    The INSERT failed, the exception text included
    `'secret_value': 'the-real-password'`, and the log panel then showed it to anybody
    with an admin session. `secret` alone does not match `secret_value` - the word
    boundary fails against the underscore - so the column name is listed in its own
    right.
    """
    import logging

    from api.logging import JsonFormatter, SecretRedactionFilter

    leaked = "the-real-mail-password"
    record = logging.LogRecord(
        "api.db",
        logging.ERROR,
        __file__,
        1,
        "INSERT failed [parameters: [{'key': 'smtp.password', 'secret_value': '%s'}]]",
        (leaked,),
        None,
    )

    SecretRedactionFilter().filter(record)
    line = JsonFormatter().format(record)

    assert leaked not in line
    assert "[redacted]" in line


async def test_storing_a_secret_without_a_key_is_answered_not_crashed(
    migrated, settings, database_url
) -> None:
    """An installation with no ENCRYPTION_KEY cannot store a credential at all.

    It used to find that out inside the INSERT, as an unhandled 500 - which is both a
    useless answer for the person typing an SMTP password and the route by which the
    password reached the log.
    """
    from httpx import ASGITransport, AsyncClient

    from api.main import create_app
    from api.models import Membership, User, Workspace
    from api.security.password import hash_password

    password = "a sentence i can actually remember"  # noqa: S105
    workspace = Workspace(name="Wagner & Partner")
    migrated.add(workspace)
    await migrated.flush()
    user = User(username="mohamed", password_hash=hash_password(password))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=workspace.id, role="admin"))
    await migrated.commit()

    # No encryption key, which is the whole point.
    app = create_app(
        settings.model_copy(update={"database_url": database_url, "encryption_key": None})
    )
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://localhost") as http:
            assert (
                await http.post(
                    "/api/auth/login", json={"username": "mohamed", "password": password}
                )
            ).status_code == 200

            response = await http.patch(
                "/api/settings", json={"values": {"smtp.password": "whatever"}}
            )

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "encryption_key_missing"
    assert "ENCRYPTION_KEY" in body["message"]


# --- The mail test button -----------------------------------------------------


async def test_the_mail_test_refuses_before_anything_is_configured(clients) -> None:
    """Told immediately, rather than by a message that never arrives."""
    response = await clients["admin"].post("/api/settings/mail/test")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mail_not_configured"


async def test_the_mail_test_needs_an_address_on_the_account(clients) -> None:
    save = await clients["admin"].patch(
        "/api/settings",
        json={"values": {"smtp.host": "mail.example.test", "smtp.from": "t@example.test"}},
    )
    assert save.status_code == 200

    response = await clients["admin"].post("/api/settings/mail/test")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_email_on_account"


async def test_the_mail_test_goes_to_the_admins_own_address(
    clients, migrated: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never a typed one: a form that mails an arbitrary address is a spam relay."""
    from sqlalchemy import update

    from api import mail

    await migrated.execute(
        update(User).where(User.username == "admin").values(email="admin@example.test")
    )
    await migrated.commit()
    save = await clients["admin"].patch(
        "/api/settings",
        json={"values": {"smtp.host": "mail.example.test", "smtp.from": "t@example.test"}},
    )
    assert save.status_code == 200

    delivered: list[str] = []

    def fake_send(config, *, to: str, subject: str, body: str) -> bool:
        delivered.append(to)
        return True

    monkeypatch.setattr(mail, "send", fake_send)

    response = await clients["admin"].post("/api/settings/mail/test")

    assert response.status_code == 200
    assert response.json() == {"sent": True, "to": "admin@example.test"}
    assert delivered == ["admin@example.test"]


async def test_a_refused_delivery_is_a_designed_answer(
    clients, migrated: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy import update

    from api import mail

    await migrated.execute(
        update(User).where(User.username == "admin").values(email="admin@example.test")
    )
    await migrated.commit()
    await clients["admin"].patch(
        "/api/settings",
        json={"values": {"smtp.host": "mail.example.test", "smtp.from": "t@example.test"}},
    )
    monkeypatch.setattr(mail, "send", lambda config, *, to, subject, body: False)

    response = await clients["admin"].post("/api/settings/mail/test")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "mail_failed"


async def test_a_viewer_may_not_send_the_test(clients) -> None:
    assert (await clients["viewer"].post("/api/settings/mail/test")).status_code == 403
