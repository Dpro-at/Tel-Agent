"""The hook bus and per-workspace switches - #242.

An app switched off in one workspace must stop reacting to that workspace's events,
and only there. The end-to-end proof, through the Apps endpoint and a real database,
is in `tests/test_apps.py`; this file holds the bus's own half: what runs, how often
the switches are read, and what happens when reading them fails or races a switch.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from api.extensions.hooks import HookBus


class Switches:
    """A stand-in for `installs.enabled_apps_reader` that counts its reads."""

    def __init__(self, enabled: dict[int, set[str]]) -> None:
        self.enabled = enabled
        self.reads = 0

    async def __call__(self, workspace_id: int) -> set[str]:
        self.reads += 1
        return set(self.enabled.get(workspace_id, ()))


def _recording_bus(switches: Switches, *slugs: str) -> tuple[HookBus, list[str]]:
    bus = HookBus(enabled_apps=switches)
    ran: list[str] = []
    for slug in slugs:
        bus.subscribe(slug, "message.received", lambda slug=slug, **_: ran.append(slug))
    return bus, ran


async def test_a_disabled_app_is_skipped_only_in_its_workspace() -> None:
    switches = Switches({1: {"slack"}, 2: {"slack", "telegram"}})
    bus, ran = _recording_bus(switches, "slack", "telegram")

    assert await bus.emit("message.received", workspace_id=1) == 1
    assert ran == ["slack"]

    ran.clear()
    assert await bus.emit("message.received", workspace_id=2) == 2
    assert ran == ["slack", "telegram"]


async def test_an_event_without_a_workspace_runs_every_listener() -> None:
    """Installation-wide events have no switch to consult."""
    switches = Switches({})
    bus, ran = _recording_bus(switches, "slack", "telegram")

    assert await bus.emit("message.received", text="hallo") == 2
    assert await bus.emit("message.received", workspace_id=None) == 2
    assert ran == ["slack", "telegram", "slack", "telegram"]
    assert switches.reads == 0


async def test_a_bus_without_a_reader_runs_every_listener() -> None:
    bus = HookBus()
    ran: list[str] = []
    bus.subscribe("slack", "message.received", lambda **_: ran.append("slack"))

    assert await bus.emit("message.received", workspace_id=7) == 1
    assert ran == ["slack"]


async def test_the_switches_are_read_once_per_workspace_not_per_listener() -> None:
    """The message path must not pay a query per listener, or per message."""
    switches = Switches({1: {"a", "b", "c"}, 2: {"a"}})
    bus, _ran = _recording_bus(switches, "a", "b", "c")

    for _ in range(5):
        await bus.emit("message.received", workspace_id=1)
    assert switches.reads == 1

    await bus.emit("message.received", workspace_id=2)
    assert switches.reads == 2


async def test_no_listeners_means_no_read() -> None:
    switches = Switches({1: {"slack"}})
    bus = HookBus(enabled_apps=switches)

    assert await bus.emit("message.received", workspace_id=1) == 0
    assert switches.reads == 0


async def test_forget_makes_the_next_event_see_the_switch() -> None:
    switches = Switches({1: {"slack"}})
    bus, ran = _recording_bus(switches, "slack")
    await bus.emit("message.received", workspace_id=1)

    switches.enabled[1] = set()
    await bus.emit("message.received", workspace_id=1)
    assert ran == ["slack", "slack"], "remembered until told otherwise"

    bus.forget(1)
    await bus.emit("message.received", workspace_id=1)
    assert ran == ["slack", "slack"]
    assert switches.reads == 2


async def test_a_read_that_straddles_a_switch_is_not_remembered() -> None:
    """A lookup started before the switch returns the old answer; putting it back into
    the cache after `forget` would undo the switch until the next restart."""
    release = asyncio.Event()
    answers = [{"slack"}, set()]
    reads = 0

    async def slow(workspace_id: int) -> set[str]:
        nonlocal reads
        answer = answers[reads]
        reads += 1
        if reads == 1:
            await release.wait()
        return answer

    bus = HookBus(enabled_apps=slow)
    ran: list[str] = []
    bus.subscribe("slack", "message.received", lambda **_: ran.append("slack"))

    straddling = asyncio.create_task(bus.emit("message.received", workspace_id=1))
    await asyncio.sleep(0)
    bus.forget(1)
    release.set()
    await straddling

    await bus.emit("message.received", workspace_id=1)
    assert ran == ["slack"], "only the event that started before the switch ran"
    assert reads == 2


async def test_a_failed_read_runs_no_app_listener_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Off must never turn back on because the database hiccuped."""
    failing = True

    async def flaky(workspace_id: int) -> set[str]:
        if failing:
            raise RuntimeError("database unreachable")
        return {"slack"}

    bus = HookBus(enabled_apps=flaky)
    ran: list[str] = []
    bus.subscribe("slack", "message.received", lambda **_: ran.append("slack"))

    with caplog.at_level(logging.ERROR, logger="api.extensions"):
        assert await bus.emit("message.received", workspace_id=1) == 0
    assert ran == []
    assert any("could not read which apps are enabled" in r.message for r in caplog.records)

    failing = False
    assert await bus.emit("message.received", workspace_id=1) == 1, "failure not cached"


async def test_cancellation_while_reading_is_not_swallowed() -> None:
    async def cancelled(workspace_id: int) -> set[str]:
        raise asyncio.CancelledError

    bus = HookBus(enabled_apps=cancelled)
    bus.subscribe("slack", "message.received", lambda **_: None)

    with pytest.raises(asyncio.CancelledError):
        await bus.emit("message.received", workspace_id=1)
