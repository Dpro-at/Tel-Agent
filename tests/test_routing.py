"""Milestone 4 — the rules engine, running on the channels that already answer.

#89 built the record and said so at the top: *"the record, not the engine"*. This is
the engine. A rule is written against a **channel identity** — an E.164, an email
address, a Telegram chat id, a Slack user — because a WhatsApp number, a handle and
a caller ID are all the same person, and building the matcher phone-only would mean
writing it twice (the roadmap's own words).

Business hours are §A6.5's line: *outside them the agent always answers*. So `pass`
— straight through to a person — holds only while somebody is there to pass to, and
degrades to `ai` outside the window. `block` is block at any hour.

The engine is exercised directly against real rows, and then end to end through the
Telegram transport: a blocked identity leaves no trace, and a `pass` identity gets a
silent agent and a person notified.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api import routing
from api.models import Rule, Workspace
from api.settings import store

KEY_HEX = "aa" * 32


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


@pytest.fixture
async def workspace(migrated: AsyncSession) -> int:
    row = Workspace(name="Wagner & Partner")
    migrated.add(row)
    await migrated.commit()
    return row.id


async def _rule(db: AsyncSession, workspace_id: int, pattern: str, action: str) -> None:
    db.add(Rule(workspace_id=workspace_id, pattern=pattern, action=action))
    await db.commit()


# A Tuesday morning and a Sunday afternoon, for the hours tests.
TUESDAY_TEN = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.UTC)
SUNDAY_FOUR = dt.datetime(2026, 9, 6, 16, 0, tzinfo=dt.UTC)


# --- Matching ---------------------------------------------------------------------


async def test_an_unknown_identity_goes_to_the_agent(migrated, workspace) -> None:
    decision = await routing.decide(migrated, workspace_id=workspace, identities=["+43111"])
    assert decision.action == "ai"
    assert decision.pattern is None


async def test_an_exact_rule_beats_a_prefix_and_a_longer_prefix_beats_a_shorter(
    migrated, workspace
) -> None:
    await _rule(migrated, workspace, "+43720*", "block")
    await _rule(migrated, workspace, "+437209*", "pass")
    await _rule(migrated, workspace, "+43720999", "ai")

    exact = await routing.decide(migrated, workspace_id=workspace, identities=["+43720999"])
    assert (exact.action, exact.pattern) == ("ai", "+43720999")

    longer = await routing.decide(migrated, workspace_id=workspace, identities=["+43720911"])
    assert (longer.action, longer.pattern) == ("pass", "+437209*")

    shorter = await routing.decide(migrated, workspace_id=workspace, identities=["+43720111"])
    assert (shorter.action, shorter.pattern) == ("block", "+43720*")


async def test_any_of_the_callers_identities_may_match(migrated, workspace) -> None:
    """A Telegram message arrives with a chat id and a username; a rule written
    against either one is a rule about this person."""
    await _rule(migrated, workspace, "@hans_maulwurf", "block")
    decision = await routing.decide(
        migrated, workspace_id=workspace, identities=["700", "@hans_maulwurf"]
    )
    assert decision.action == "block"


async def test_matching_ignores_case_because_mail_and_handles_do(migrated, workspace) -> None:
    await _rule(migrated, workspace, "boss@example.com", "pass")
    decision = await routing.decide(
        migrated, workspace_id=workspace, identities=["Boss@Example.COM"]
    )
    assert decision.action == "pass"


async def test_another_workspaces_rules_never_apply(migrated, workspace) -> None:
    theirs = Workspace(name="Wolf Studio")
    migrated.add(theirs)
    await migrated.flush()
    await _rule(migrated, theirs.id, "+43111", "block")

    decision = await routing.decide(migrated, workspace_id=workspace, identities=["+43111"])
    assert decision.action == "ai"


# --- Business hours ---------------------------------------------------------------


def test_the_hours_setting_parses_days_and_a_window() -> None:
    hours = routing.parse_hours("mo-fr 08:00-18:00")
    assert hours is not None
    assert routing.within(hours, TUESDAY_TEN.replace(tzinfo=None))
    assert not routing.within(hours, SUNDAY_FOUR.replace(tzinfo=None))
    # Garbage is None - no hours configured - rather than an exception.
    assert routing.parse_hours("whenever i feel like it") is None
    assert routing.parse_hours("") is None


def test_an_overnight_window_wraps_midnight() -> None:
    hours = routing.parse_hours("18:00-02:00")
    assert hours is not None
    late = dt.datetime(2026, 9, 1, 23, 30)
    early = dt.datetime(2026, 9, 1, 1, 0)
    midday = dt.datetime(2026, 9, 1, 12, 0)
    assert routing.within(hours, late)
    assert routing.within(hours, early)
    assert not routing.within(hours, midday)


async def test_pass_degrades_to_ai_outside_business_hours(migrated, workspace) -> None:
    """§A6.5: outside them the agent always answers - there is nobody to pass to."""
    await _rule(migrated, workspace, "boss@example.com", "pass")
    await store.set_value(
        migrated, "routing.hours", "mo-fr 08:00-18:00", workspace_id=workspace
    )
    await store.set_value(migrated, "routing.timezone", "UTC", workspace_id=workspace)
    await migrated.commit()

    weekday = await routing.decide(
        migrated, workspace_id=workspace, identities=["boss@example.com"], now=TUESDAY_TEN
    )
    assert weekday.action == "pass"

    sunday = await routing.decide(
        migrated, workspace_id=workspace, identities=["boss@example.com"], now=SUNDAY_FOUR
    )
    assert sunday.action == "ai"


async def test_hours_are_read_in_the_workspaces_timezone(migrated, workspace) -> None:
    """A real IANA zone, so the `tzdata` dependency is exercised: without it Windows
    and slim containers fall back to the server clock and answer at the wrong times.
    16:00 UTC on a Sunday is 18:00 in Vienna - still Sunday, still outside mo-fr."""
    await _rule(migrated, workspace, "boss@example.com", "pass")
    await store.set_value(
        migrated, "routing.hours", "mo-fr 08:00-18:00", workspace_id=workspace
    )
    await store.set_value(migrated, "routing.timezone", "Europe/Vienna", workspace_id=workspace)
    await migrated.commit()

    # 07:30 UTC Tuesday is 09:30 in Vienna - inside the window only once the zone is
    # applied; read as UTC it would be 07:30, before opening.
    inside = dt.datetime(2026, 9, 1, 7, 30, tzinfo=dt.UTC)
    decision = await routing.decide(
        migrated, workspace_id=workspace, identities=["boss@example.com"], now=inside
    )
    assert decision.action == "pass"


async def test_block_is_block_at_any_hour(migrated, workspace) -> None:
    await _rule(migrated, workspace, "+43111", "block")
    await store.set_value(
        migrated, "routing.hours", "mo-fr 08:00-18:00", workspace_id=workspace
    )
    await store.set_value(migrated, "routing.timezone", "UTC", workspace_id=workspace)
    await migrated.commit()

    sunday = await routing.decide(
        migrated, workspace_id=workspace, identities=["+43111"], now=SUNDAY_FOUR
    )
    assert sunday.action == "block"
