"""The generic channel routes — one card contract, and one public door, for every kind.

Fifteen channels share these four routes, so what they promise is tested once here
rather than fifteen times over. The channel under test is a stub registered into the
registry by a fixture: it declares one secret field, one shown field and one optional
one, and it answers a fake platform. `sms` is its kind because the kind has to exist
in the database and a made-up one does not.

The rules being proved are §B9's, the same ones `api/routes/discord_channel.py`
enforces by hand: the secret goes in and only a mask comes out, an empty string clears
it and takes the channel down with it, an echoed mask is ignored, and nothing is
stored at all when the installation has no key. Behind that, the door's rule: one
refusal for every reason.
"""

from __future__ import annotations

import json
import types

import httpx
import pytest
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.channels import generic
from api.channels.setup import Field, Setup
from api.config import Settings
from api.main import create_app
from api.models import Channel, Membership, User, Workspace
from api.security.password import hash_password

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "bb" * 32
API_KEY = "stub-api-key-9042"


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


class FakePlatform:
    """The stub channel's platform: it knows one identity and can refuse."""

    def __init__(self) -> None:
        self.refuse = False
        self.delivered: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.refuse:
            return httpx.Response(401, json={"message": "unauthorised"})
        if request.url.path == "/me":
            return httpx.Response(200, json={"name": "stub-bot"})
        if request.url.path == "/send":
            self.delivered.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"message": "unknown"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://stub.invalid", transport=httpx.MockTransport(self.handler)
        )


def _stub_module(platform: FakePlatform) -> types.ModuleType:
    module = types.ModuleType("api.channels._stub")
    module.KIND = "sms"
    module.INBOUND = "door"
    module.SETUP = Setup(
        kind="sms",
        title="SMS",
        note="A stub for the contract every card keeps.",
        guide_url="https://example.invalid/guide",
        fields=(
            Field("api_key", "API key", secret=True, help="From the provider console."),
            Field("account", "Account", secret=False, placeholder="AC-000"),
            Field("label", "Label", secret=False, required=False),
        ),
        verified_live=False,
    )
    module.make_client = platform.client

    async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
        response = await client.get("/me")
        if response.status_code >= 400:
            raise generic.ChannelRefused("the platform said no")
        return str(response.json()["name"])

    async def receive(db, channel, request) -> JSONResponse:
        return JSONResponse({"heard": channel.kind})

    module.probe = probe
    module.receive = receive
    return module


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> FakePlatform:
    platform = FakePlatform()
    monkeypatch.setitem(generic.CHANNELS, "sms", _stub_module(platform))
    return platform


@pytest.fixture
async def stage(
    migrated: AsyncSession,
    settings: Settings,
    database_url: str,
    registered: FakePlatform,
):
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    for username, role in (("mohamed", "admin"), ("lukas", "viewer")):
        user = User(username=username, password_hash=hash_password(PASSWORD))
        migrated.add(user)
        await migrated.flush()
        migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role=role))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    clients: dict[str, AsyncClient] = {}
    async with app.router.lifespan_context(app):
        asgi = ASGITransport(app=app, raise_app_exceptions=False)
        for username in ("mohamed", "lukas"):
            http = AsyncClient(transport=asgi, base_url="http://localhost")
            assert (
                await http.post(
                    "/api/auth/login", json={"username": username, "password": PASSWORD}
                )
            ).status_code == 200
            clients[username] = http
        public = AsyncClient(transport=asgi, base_url="http://localhost")
        try:
            yield clients, public, registered, migrated
        finally:
            for http in clients.values():
                await http.aclose()
            await public.aclose()


async def _row(db: AsyncSession) -> Channel:
    db.expire_all()
    return await db.scalar(select(Channel).where(Channel.kind == "sms"))


# --- The card ---------------------------------------------------------------------


async def test_a_channel_nobody_has_configured_reads_as_its_declaration(stage) -> None:
    clients, _, _, _ = stage
    answer = await clients["mohamed"].get("/api/channels/sms")
    assert answer.status_code == 200, answer.text
    body = answer.json()

    assert body["setup"]["title"] == "SMS"
    assert [field["name"] for field in body["setup"]["fields"]] == [
        "api_key",
        "account",
        "label",
    ]
    assert body["enabled"] is False
    assert body["status"] == "disabled"
    assert body["values"] == {}
    assert body["previews"] == {"api_key": None}
    assert body["identity"] is None
    assert body["verified_live"] is False
    # A door channel says where the platform must call it.
    assert "/public/sms/" in body["webhook_url"]


async def test_an_unknown_kind_is_not_a_channel(stage) -> None:
    clients, _, _, _ = stage
    assert (await clients["mohamed"].get("/api/channels/nothing")).status_code == 404
    assert (
        await clients["mohamed"].put("/api/channels/nothing", json={"enabled": True})
    ).status_code == 404


async def test_the_secret_goes_in_and_only_a_mask_comes_out(stage) -> None:
    clients, _, _, _ = stage
    saved = await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": API_KEY, "account": "AC-77"}}
    )
    assert saved.status_code == 200, saved.text
    assert API_KEY not in json.dumps(saved.json())
    assert saved.json()["previews"]["api_key"].endswith("9042")
    # A shown field is shown; that is what makes it not a secret.
    assert saved.json()["values"]["account"] == "AC-77"


async def test_an_echoed_mask_is_not_a_new_secret(stage) -> None:
    clients, _, _, _ = stage
    await clients["mohamed"].put("/api/channels/sms", json={"fields": {"api_key": API_KEY}})
    echoed = await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": "••••9042"}}
    )
    assert echoed.json()["previews"]["api_key"].endswith("9042")


async def test_removing_the_secret_switches_the_channel_off_with_it(stage) -> None:
    clients, _, _, db = stage
    await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": API_KEY, "account": "AC-77"}}
    )
    on = await clients["mohamed"].put("/api/channels/sms", json={"enabled": True})
    assert on.json()["enabled"] is True

    cleared = await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": ""}}
    )
    assert cleared.json()["previews"]["api_key"] is None
    assert cleared.json()["enabled"] is False
    assert (await _row(db)).status == "disabled"


async def test_switching_on_without_the_credentials_is_refused(stage) -> None:
    clients, _, _, _ = stage
    refused = await clients["mohamed"].put("/api/channels/sms", json={"enabled": True})
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "missing_credentials"


async def test_a_channel_with_every_required_field_can_be_switched_on(stage) -> None:
    """The optional field is optional — an empty one must not hold the channel down."""
    clients, _, _, _ = stage
    await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": API_KEY, "account": "AC-77"}}
    )
    on = await clients["mohamed"].put("/api/channels/sms", json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True


async def test_nothing_is_stored_when_the_installation_has_no_key(
    stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked before the write, not discovered inside it — `api/security/crypto.py`."""
    clients, _, _, _ = stage
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    reset_key_cache()

    refused = await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": API_KEY}}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "encryption_key_missing"
    assert (await clients["mohamed"].get("/api/channels/sms")).json()["previews"] == {
        "api_key": None
    }


async def test_a_viewer_reads_and_never_writes(stage) -> None:
    clients, _, _, _ = stage
    assert (await clients["lukas"].get("/api/channels/sms")).status_code == 200
    assert (
        await clients["lukas"].put("/api/channels/sms", json={"enabled": False})
    ).status_code == 403
    assert (await clients["lukas"].post("/api/channels/sms/test")).status_code == 403


async def test_the_test_button_reports_the_platforms_own_identity(stage) -> None:
    clients, _, _, _ = stage
    await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": API_KEY, "account": "AC-77"}}
    )
    answer = await clients["mohamed"].post("/api/channels/sms/test")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "identity": "stub-bot"}
    read = (await clients["mohamed"].get("/api/channels/sms")).json()
    assert read["identity"] == "stub-bot"


async def test_a_refused_credential_names_the_kind_and_confirms_nothing_else(stage) -> None:
    clients, _, platform, _ = stage
    await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": API_KEY, "account": "AC-77"}}
    )
    platform.refuse = True
    answer = await clients["mohamed"].post("/api/channels/sms/test")
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "sms_refused"


async def test_the_test_button_needs_the_credentials_first(stage) -> None:
    clients, _, _, _ = stage
    answer = await clients["mohamed"].post("/api/channels/sms/test")
    assert answer.status_code == 409
    assert answer.json()["error"]["code"] == "missing_credentials"


# --- The door ---------------------------------------------------------------------


async def _live_path(clients, db: AsyncSession) -> str:
    await clients["mohamed"].put(
        "/api/channels/sms", json={"fields": {"api_key": API_KEY, "account": "AC-77"}}
    )
    await clients["mohamed"].put("/api/channels/sms", json={"enabled": True})
    return (await _row(db)).webhook_path


async def test_a_live_door_hands_the_delivery_to_its_channel(stage) -> None:
    clients, public, _, db = stage
    path = await _live_path(clients, db)
    answer = await public.post(f"/public/sms/{path}", json={"anything": True})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"heard": "sms"}


async def test_an_unknown_address_a_wrong_kind_and_a_disabled_channel_read_alike(
    stage,
) -> None:
    """One refusal for every reason — the door leaks nothing about what is behind it."""
    clients, public, _, db = stage
    path = await _live_path(clients, db)

    refusals = [
        await public.post("/public/sms/not-an-address", json={}),
        await public.post(f"/public/telegram/{path}", json={}),
    ]
    await clients["mohamed"].put("/api/channels/sms", json={"enabled": False})
    refusals.append(await public.post(f"/public/sms/{path}", json={}))

    for refused in refusals:
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "not_recognised"


async def test_the_door_answers_a_get_the_same_way_it_answers_a_post(stage) -> None:
    """Platforms verify a webhook with a GET before they ever send one."""
    clients, public, _, db = stage
    path = await _live_path(clients, db)
    assert (await public.get(f"/public/sms/{path}")).json() == {"heard": "sms"}
    assert (await public.get("/public/sms/not-an-address")).status_code == 403
