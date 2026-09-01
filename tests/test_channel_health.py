"""Milestone 9 — the transports say how they are, the screen shows it, the tray shouts.

Three pieces under test. The registry: transitions raise exactly one alert going
down and one quiet note coming back, and a retry loop mid-outage raises nothing
more. The status rows: several channels of one kind fold to the worst state, silence
since restart reads as `degraded` with its reason, and disabled-only kinds read
`not_configured`. The wiring: a failing poll actually lands in the registry and the
tray, a signed webhook delivery counts as life, and the five-minute probe asks the
webhook platforms directly.
"""

from __future__ import annotations

import json

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.channels import health, telegram, whatsapp
from api.config import Settings
from api.jobs.builtin import _probe_webhook_channels
from api.main import create_app
from api.models import Channel, Membership, Notification, User, Workspace
from api.security.password import hash_password
from api.system import status as system_status

PASSWORD = "a sentence i can actually remember"  # noqa: S105
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def configured_key(monkeypatch: pytest.MonkeyPatch):
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    health.reset()
    yield
    get_settings.cache_clear()
    reset_key_cache()
    health.reset()


@pytest.fixture
async def stage(migrated: AsyncSession, settings: Settings, database_url: str):
    """One workspace, one active telegram channel, one active whatsapp channel."""
    mine = Workspace(name="Wagner & Partner")
    migrated.add(mine)
    await migrated.flush()

    tg = Channel(
        workspace_id=mine.id,
        kind="telegram",
        name="Telegram",
        credentials_encrypted="tg-token",
        settings_json={},
        status="active",
    )
    wa = Channel(
        workspace_id=mine.id,
        kind="whatsapp",
        name="WhatsApp",
        credentials_encrypted=json.dumps({"access_token": "t", "app_secret": "s"}),
        settings_json={"phone_number_id": "108500", "verify_token": "v"},
        status="active",
    )
    off = Channel(workspace_id=mine.id, kind="discord", name="Discord", status="disabled")
    migrated.add_all([tg, wa, off])

    user = User(username="mohamed", password_hash=hash_password(PASSWORD))
    migrated.add(user)
    await migrated.flush()
    migrated.add(Membership(user_id=user.id, workspace_id=mine.id, role="admin"))
    await migrated.commit()

    app = create_app(settings.model_copy(update={"database_url": database_url}))
    async with app.router.lifespan_context(app):
        http = AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://localhost",
        )
        assert (
            await http.post(
                "/api/auth/login", json={"username": "mohamed", "password": PASSWORD}
            )
        ).status_code == 200
        try:
            yield http, {"telegram": tg.id, "whatsapp": wa.id}, migrated
        finally:
            await http.aclose()


async def _tray(db: AsyncSession) -> list[Notification]:
    db.expire_all()
    return list((await db.execute(select(Notification).order_by(Notification.id))).scalars())


# --- The registry and its alerts ------------------------------------------------


async def test_going_down_raises_one_alert_and_a_retry_loop_raises_no_more(stage) -> None:
    _, ids, db = stage
    channel = await db.scalar(select(Channel).where(Channel.id == ids["telegram"]))

    await health.report_down(db, channel, detail="getUpdates: 401: Unauthorized")
    await health.report_down(db, channel, detail="getUpdates: 401: Unauthorized")
    await health.report_down(db, channel, detail="getUpdates: 401: Unauthorized")

    rows = await _tray(db)
    assert [row.message_key for row in rows] == ["channel_down"]
    assert rows[0].needs_decision is True
    assert rows[0].params == {"channel": "telegram"}
    # The reason travels as machine output, redacted and trimmed like every detail.
    assert "401" in (rows[0].detail or "")


async def test_coming_back_says_so_quietly(stage) -> None:
    _, ids, db = stage
    channel = await db.scalar(select(Channel).where(Channel.id == ids["telegram"]))

    await health.report_down(db, channel, detail="unreachable")
    await health.report_ok(db, channel)
    await health.report_ok(db, channel)

    rows = await _tray(db)
    assert [row.message_key for row in rows] == ["channel_down", "channel_recovered"]
    assert rows[1].needs_decision is False


async def test_being_up_all_along_says_nothing(stage) -> None:
    _, ids, db = stage
    channel = await db.scalar(select(Channel).where(Channel.id == ids["telegram"]))
    await health.report_ok(db, channel)
    await health.report_ok(db, channel)
    assert await _tray(db) == []


# --- The status rows ------------------------------------------------------------


def _row(report: dict, service_id: str) -> dict:
    return next(s for s in report["services"] if s["id"] == service_id)


async def test_the_screen_reads_the_transports_own_reports(stage) -> None:
    _, ids, db = stage
    from api.config import get_settings

    channel = await db.scalar(select(Channel).where(Channel.id == ids["telegram"]))
    await health.report_ok(db, channel)
    health.note_reply("telegram", ids["telegram"], 1234.5)

    report = await system_status.collect(db, get_settings())
    telegram_row = _row(report, "channel_telegram")
    assert telegram_row["state"] == "ok"
    assert telegram_row["latency_ms"] == 1234.5

    # Enabled, but nothing heard since this process started: degraded, with the
    # reason - which after a restart is exactly true for as long as it lasts.
    whatsapp_row = _row(report, "channel_whatsapp")
    assert whatsapp_row["state"] == "degraded"
    assert "nothing heard" in (whatsapp_row["detail"] or "")

    # Every channel of the kind disabled: switched off is not set up.
    assert _row(report, "channel_discord")["state"] == "not_configured"

    # A kind with no channel row at all gets no row - and no lie either way.
    assert not any(s["id"] == "channel_slack" for s in report["services"])


async def test_a_down_channel_turns_the_installations_verdict(stage) -> None:
    _, ids, db = stage
    from api.config import get_settings

    channel = await db.scalar(select(Channel).where(Channel.id == ids["telegram"]))
    await health.report_down(db, channel, detail="unreachable")

    report = await system_status.collect(db, get_settings())
    assert _row(report, "channel_telegram")["state"] == "down"
    assert report["verdict"] == "down"


# --- The wiring -----------------------------------------------------------------


async def test_a_failing_poll_lands_in_the_registry_and_the_tray(stage, monkeypatch) -> None:
    """Driven through the real transport, not the registry directly."""
    _, ids, db = stage

    def refusing_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://telegram.test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"ok": False, "description": "Unauthorized"}
                )
            ),
        )

    async with refusing_client() as client:
        await telegram.poll_once(db, client)

    assert health.snapshot()[("telegram", ids["telegram"])].state == "down"
    assert [row.message_key for row in await _tray(db)] == ["channel_down"]


async def test_a_signed_delivery_counts_as_life(stage) -> None:
    _, ids, db = stage
    channel = await db.scalar(select(Channel).where(Channel.id == ids["whatsapp"]))
    await whatsapp.ingest(db, channel, {"entry": []})
    assert health.snapshot()[("whatsapp", ids["whatsapp"])].state == "ok"


async def test_the_probe_asks_the_webhook_platforms_directly(stage, monkeypatch) -> None:
    _, ids, db = stage

    def answering_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://graph.test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"display_phone_number": "+43"})
            ),
        )

    monkeypatch.setattr(whatsapp, "make_client", answering_client)
    await _probe_webhook_channels(db)
    assert health.snapshot()[("whatsapp", ids["whatsapp"])].state == "ok"

    def refusing_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://graph.test",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    401, json={"error": {"message": "Invalid OAuth token"}}
                )
            ),
        )

    monkeypatch.setattr(whatsapp, "make_client", refusing_client)
    await _probe_webhook_channels(db)
    assert health.snapshot()[("whatsapp", ids["whatsapp"])].state == "down"
    assert "channel_down" in [row.message_key for row in await _tray(db)]
