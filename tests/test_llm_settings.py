"""Where the model's key lives, and which source wins — §B9.2.

The rule these exist to satisfy is the one CLAUDE.md states twice: a provider key is a
credential the *user* enters, so it belongs in an encrypted column and not in `.env`,
and `.env` stops being the answer at Milestone 1. What is tested here is therefore not
"can a value be stored" — `test_settings_store.py` owns that — but the three things a
person can actually be hurt by: which source wins, what a half-finished configuration
does, and whether a saved key can be read back out.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from agent.config import ConfigurationError
from agent.providers.llm.base import Message, TextDelta
from api import llm
from api.config import Settings
from api.main import create_app
from api.models import Membership, User, Workspace
from api.security.password import hash_password
from api.settings import store

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32
REAL_KEY = "the-actual-provider-credential-value"


@pytest.fixture
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


async def _configure(db: AsyncSession, **values: str) -> None:
    for field, value in values.items():
        await store.set_value(db, f"llm.{field}", value)
    await db.commit()


# --- Which source wins -------------------------------------------------------


async def test_nothing_anywhere_is_a_supported_state(migrated: AsyncSession) -> None:
    """An installation with no model still answers, in words. Not a failure."""
    assert await llm.resolve(migrated) is None


async def test_the_environment_alone_still_configures_a_model(
    migrated: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installer's path has to keep working, or every existing `.env` breaks on
    upgrade - and at Milestone 11 it is the only path there is."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "from-the-file")
    monkeypatch.setenv("LLM_API_KEY", "key-from-the-file")

    settings = await llm.resolve(migrated)
    assert settings is not None
    assert settings.model == "from-the-file"


async def test_the_store_beats_the_environment(
    migrated: AsyncSession, monkeypatch: pytest.MonkeyPatch, configured_key
) -> None:
    """P3's point, on the setting it matters most for: `.env` is what an installer
    wrote once, the store is what the owner changed on the screen afterwards."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "from-the-file")
    monkeypatch.setenv("LLM_API_KEY", "key-from-the-file")

    await _configure(migrated, provider="openai", model="from-the-screen", api_key=REAL_KEY)

    settings = await llm.resolve(migrated)
    assert settings is not None
    assert settings.model == "from-the-screen"
    assert settings.api_key == REAL_KEY


async def test_one_field_saved_on_the_screen_completes_the_environment(
    migrated: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The migration §B9.2 describes, in the shape it actually happens.

    Somebody running on `.env` opens the screen and changes only the model name. The
    key they never retyped has to keep working, or the move costs them an outage - so
    the two sources merge per value rather than one of them winning whole.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "the-old-model")
    monkeypatch.setenv("LLM_API_KEY", "key-from-the-file")

    await _configure(migrated, model="the-new-model")

    settings = await llm.resolve(migrated)
    assert settings is not None
    assert settings.model == "the-new-model"
    assert settings.api_key == "key-from-the-file"


async def test_a_saved_key_takes_effect_without_a_restart(
    migrated: AsyncSession, configured_key
) -> None:
    """§B9.2's table says so in one word: *immediately*.

    A cache here would quietly turn that into "after the next restart", which is the
    whole reason the key was moved out of `.env` in the first place.
    """
    await _configure(migrated, provider="openai", model="m", api_key="first")
    first = await llm.resolve(migrated)
    assert first is not None and first.api_key == "first"

    await _configure(migrated, api_key="second")
    second = await llm.resolve(migrated)
    assert second is not None and second.api_key == "second"


# --- Half a configuration ----------------------------------------------------


async def test_half_a_configuration_is_refused_in_the_screens_words(
    migrated: AsyncSession,
) -> None:
    """A provider named with nothing behind it is somebody who has not finished.

    Falling back to "no model" would look exactly like the model answering badly. And
    the refusal names *the field on the screen*: `LLM_API_KEY` is not something a
    person who typed into a form can act on.
    """
    await _configure(migrated, provider="openai")

    with pytest.raises(ConfigurationError) as refused:
        await llm.resolve(migrated)

    assert "the model name" in str(refused.value)
    assert "the API key" in str(refused.value)
    assert "LLM_" not in str(refused.value)


async def test_the_health_row_carries_the_reason_rather_than_a_colour(
    migrated: AsyncSession,
) -> None:
    """An installation that thinks it has a model and does not is the case an owner
    cannot diagnose from outside. This row is where they see it."""
    assert await llm.describe(migrated) == ("not_configured", None)

    await _configure(migrated, provider="openai")
    state, detail = await llm.describe(migrated)
    assert state == "down"
    assert detail and "the API key" in detail


async def test_the_health_row_never_carries_the_key(
    migrated: AsyncSession, configured_key
) -> None:
    await _configure(migrated, provider="openai", model="gpt-4o-mini", api_key=REAL_KEY)
    state, detail = await llm.describe(migrated)
    assert state == "ok"
    assert detail is not None
    assert "gpt-4o-mini" in detail
    assert REAL_KEY not in detail


# --- The endpoints -----------------------------------------------------------


@pytest.fixture
async def clients(
    migrated: AsyncSession, settings: Settings, database_url: str, configured_key
):
    """An admin and a viewer, signed in, against an app that can encrypt."""
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


async def test_the_key_is_masked_on_the_way_out(clients) -> None:
    """§B9: never returned in full to a client. The screen shows the last four so a
    person can tell which key is in force without being handed one."""
    saved = await clients["admin"].patch(
        "/api/settings", json={"values": {"llm.api_key": REAL_KEY}}
    )
    assert saved.status_code == 200

    rows = (await clients["admin"].get("/api/settings")).json()
    key_row = next(row for row in rows if row["key"] == "llm.api_key")
    assert key_row["secret"] is True
    assert key_row["value"] != REAL_KEY
    assert REAL_KEY not in str(rows)
    assert str(key_row["value"]).endswith(REAL_KEY[-4:])


async def test_saving_the_mask_back_does_not_overwrite_the_key(
    clients, migrated: AsyncSession
) -> None:
    """The failure this prevents: an admin edits the model name, submits the form with
    the key field still showing its mask, and replaces a live credential with bullets."""
    await clients["admin"].patch("/api/settings", json={"values": {"llm.api_key": REAL_KEY}})
    rows = (await clients["admin"].get("/api/settings")).json()
    mask = next(row for row in rows if row["key"] == "llm.api_key")["value"]

    answer = await clients["admin"].patch(
        "/api/settings", json={"values": {"llm.model": "gpt-4o-mini", "llm.api_key": mask}}
    )
    assert answer.status_code == 200
    assert answer.json()["ignored_masked"] == ["llm.api_key"]

    assert await store.get(migrated, "llm.api_key") == REAL_KEY


async def test_a_viewer_may_not_test_the_model(clients) -> None:
    assert (await clients["viewer"].post("/api/settings/llm/test")).status_code == 403


async def test_the_test_refuses_before_a_model_is_connected(clients) -> None:
    answer = await clients["admin"].post("/api/settings/llm/test")
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "llm_not_configured"


async def test_the_test_names_the_field_that_is_empty(clients) -> None:
    await clients["admin"].patch("/api/settings", json={"values": {"llm.provider": "openai"}})

    answer = await clients["admin"].post("/api/settings/llm/test")
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "llm_incomplete"
    assert "the API key" in answer.json()["error"]["message"]


class _Stub:
    """One token, and a record of whether the consumer closed the stream."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.closed = False

    async def stream(self, messages: list[Message], tools=None) -> AsyncIterator[TextDelta]:
        if self.error is not None:
            raise self.error
        try:
            yield TextDelta("ready")
            yield TextDelta("this should never be reached")
        finally:
            self.closed = True


async def test_the_test_asks_for_one_token_and_closes_the_stream(
    clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reachability is the question, and it is answered by the first event.

    The close is asserted rather than assumed: it is the cancellation path Rule 3 says
    must work, exercised here on the cheapest request in the product.
    """
    import agent.providers.llm as llm_package

    stub = _Stub()
    monkeypatch.setattr(llm_package, "provider_for", lambda _settings: stub)
    await clients["admin"].patch(
        "/api/settings",
        json={"values": {"llm.provider": "openai", "llm.model": "m", "llm.api_key": REAL_KEY}},
    )

    answer = await clients["admin"].post("/api/settings/llm/test")
    assert answer.status_code == 200
    assert answer.json() == {
        "reached": True,
        "model": "m",
        "base_url": "https://api.openai.com/v1",
    }
    assert stub.closed is True


async def test_a_rejected_key_is_a_designed_answer_not_a_traceback(
    clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected key is what "the setting looks right" actually looks like."""
    import agent.providers.llm as llm_package

    refusal = httpx.HTTPStatusError(
        "unauthorized",
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
        response=httpx.Response(401),
    )
    monkeypatch.setattr(llm_package, "provider_for", lambda _settings: _Stub(refusal))
    await clients["admin"].patch(
        "/api/settings",
        json={"values": {"llm.provider": "openai", "llm.model": "m", "llm.api_key": "wrong"}},
    )

    answer = await clients["admin"].post("/api/settings/llm/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "llm_refused"
    assert "401" in answer.json()["error"]["message"]


async def test_an_endpoint_that_does_not_answer_is_a_designed_answer(
    clients, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent.providers.llm as llm_package

    monkeypatch.setattr(
        llm_package,
        "provider_for",
        lambda _settings: _Stub(httpx.ConnectError("nothing there")),
    )
    await clients["admin"].patch(
        "/api/settings",
        json={
            "values": {
                "llm.provider": "openai",
                "llm.model": "m",
                "llm.api_key": "k",
                "llm.base_url": "http://127.0.0.1:1/v1",
            }
        },
    )

    answer = await clients["admin"].post("/api/settings/llm/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "llm_unreachable"


async def test_a_key_that_cannot_be_decrypted_is_a_row_rather_than_a_crash(
    migrated: AsyncSession, monkeypatch: pytest.MonkeyPatch, configured_key
) -> None:
    """ENCRYPTION_KEY rotated or lost: every stored credential is unreadable at once.

    The health screen is exactly where somebody goes when credentials stop working, so
    it says so rather than returning a 500 at the least useful possible moment. The
    reply path still raises - a turn that cannot read its key must not answer as though
    no model were configured.
    """
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache
    from api.security.crypto import DecryptionFailed

    await _configure(migrated, provider="openai", model="m", api_key=REAL_KEY)

    monkeypatch.setenv("ENCRYPTION_KEY", "bb" * 32)
    get_settings.cache_clear()
    reset_key_cache()
    migrated.expunge_all()

    state, detail = await llm.describe(migrated)
    assert state == "down"
    assert detail is not None
    assert "ENCRYPTION_KEY" in detail

    migrated.expunge_all()
    with pytest.raises(DecryptionFailed):
        await llm.resolve(migrated)


async def test_the_test_button_answers_an_unreadable_key_too(
    clients, migrated: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The health row and this button report one fault the same way, because two
    wordings for one fault read as two faults."""
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    await clients["admin"].patch(
        "/api/settings",
        json={"values": {"llm.provider": "openai", "llm.model": "m", "llm.api_key": REAL_KEY}},
    )

    monkeypatch.setenv("ENCRYPTION_KEY", "cc" * 32)
    get_settings.cache_clear()
    reset_key_cache()

    answer = await clients["admin"].post("/api/settings/llm/test")
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "llm_key_unreadable"
    assert answer.json()["error"]["message"] == llm.UNREADABLE_KEY
