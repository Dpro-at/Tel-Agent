"""The extension contract — P5, implementing D-031.

Extensions run in this process (the v1 decision), so the tests that matter most are the
ones about *containment*: a broken extension must be refused at load, and a broken hook
must not take down whatever emitted the event. Everything else here is the contract
being honest — a manifest that lies is caught, and the core is held to the same rules.
"""

from __future__ import annotations

import sys
import types

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.extensions.builtin import BUILTIN
from api.extensions.hooks import EVENTS, HookBus, UnknownEvent
from api.extensions.manifest import InvalidManifest, parse
from api.extensions.registry import Registry, sync_catalogue
from api.models import App

GOOD = {
    "slug": "telegram",
    "name": "Telegram",
    "version": "1.2.0",
    "origin": "community",
    "category": "channels",
    "scopes": ["messages.write"],
    "hooks": ["message.received"],
    "migration_prefix": "telegram_",
}


def _module(name: str, manifest: dict | None, register=None) -> str:
    """Install a throwaway extension module and return its import path."""
    module = types.ModuleType(name)
    if manifest is not None:
        module.MANIFEST = manifest
    if register is not None:
        module.register = register
    sys.modules[name] = module
    return name


@pytest.fixture(autouse=True)
def _clean_modules():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)


# --- The manifest ------------------------------------------------------------


def test_a_good_manifest_parses() -> None:
    manifest = parse(GOOD)

    assert manifest.slug == "telegram"
    assert manifest.scopes == ("messages.write",)
    assert manifest.migration_prefix == "telegram_"


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("slug", "Telegram", "lowercase"),
        ("slug", "9live", "lowercase"),
        ("version", "1.2", "1.2.3"),
        ("origin", "vendor", "origin"),
        ("category", "misc", "category"),
    ],
)
def test_a_malformed_manifest_names_the_field(field: str, value: str, expected: str) -> None:
    """A manifest is usually written by somebody who is not us. "Invalid manifest" as
    the whole message costs an hour."""
    with pytest.raises(InvalidManifest) as error:
        parse({**GOOD, field: value})

    assert expected in str(error.value)


def test_an_undeclarable_scope_is_refused() -> None:
    with pytest.raises(InvalidManifest) as error:
        parse({**GOOD, "scopes": ["messages.write", "database.drop"]})

    assert "database.drop" in str(error.value)


def test_a_migration_prefix_must_be_a_prefix() -> None:
    """So an extension's tables can never be mistaken for a core table."""
    with pytest.raises(InvalidManifest):
        parse({**GOOD, "migration_prefix": "messages"})


# --- Loading, and refusing ---------------------------------------------------


def test_a_working_extension_loads_and_subscribes() -> None:
    seen: list[str] = []

    def register(context) -> None:
        context.on("message.received", lambda **kw: seen.append(kw["text"]))

    registry = Registry()
    entry = registry.load(_module("ext_ok", GOOD, register))

    assert entry is not None
    assert registry.failed == []
    assert [listener.slug for listener in registry.bus.listeners_for("message.received")] == [
        "telegram"
    ]


@pytest.mark.parametrize(
    ("name", "manifest", "register", "reason"),
    [
        ("ext_no_manifest", None, lambda context: None, "no MANIFEST"),
        # A slug alone: it fails on the slug pattern before any missing field is
        # reached, which is the message a manifest author actually needs.
        ("ext_bad_slug", {"slug": "x"}, lambda context: None, "lowercase"),
        ("ext_missing_field", {"slug": "telegram"}, lambda context: None, "missing"),
        ("ext_no_register", GOOD, None, "no register()"),
    ],
)
def test_a_broken_extension_is_refused_not_raised(
    name: str, manifest: dict | None, register, reason: str
) -> None:
    """Startup must survive it. A server that dies because one community extension has
    a typo is a product only we can extend."""
    registry = Registry()

    assert registry.load(_module(name, manifest, register)) is None
    assert len(registry.failed) == 1
    assert reason in registry.failed[0].reason


def test_an_extension_that_throws_at_import_is_refused() -> None:
    registry = Registry()

    # A module path that does not exist stands in for one that raises on import: both
    # arrive here as an exception from importlib.
    assert registry.load("api.extensions.builtin.does_not_exist") is None
    assert "import failed" in registry.failed[0].reason


def test_a_register_that_raises_leaves_no_listeners_behind() -> None:
    """Half a registration is worse than none: the extension is not loaded, but its
    listeners would still fire."""

    def register(context) -> None:
        context.on("message.received", lambda **kw: None)
        raise RuntimeError("boom")

    registry = Registry()
    registry.load(_module("ext_throws", GOOD, register))

    assert registry.loaded == {}
    assert registry.bus.listeners_for("message.received") == []


def test_a_manifest_that_lies_about_its_hooks_is_refused() -> None:
    """The manifest is what the catalogue shows, so it has to be true."""

    def register(context) -> None:
        context.on("conversation.started", lambda **kw: None)

    registry = Registry()
    registry.load(_module("ext_liar", GOOD, register))  # declares message.received only

    assert registry.loaded == {}
    assert "undeclared" in registry.failed[0].reason
    assert registry.bus.listeners_for("conversation.started") == []


def test_two_extensions_cannot_claim_one_slug() -> None:
    registry = Registry()
    registry.load(_module("ext_first", GOOD, lambda context: None))
    registry.load(_module("ext_second", GOOD, lambda context: None))

    assert len(registry.loaded) == 1
    assert "already loaded" in registry.failed[0].reason


# --- Containment: the point of the in-process trade ---------------------------


async def test_a_failing_hook_does_not_break_the_emitter() -> None:
    """A message arrives, is stored, and the arrival is announced. If an extension
    throws while reacting, the message is still stored."""
    bus = HookBus()
    ran: list[str] = []

    def explodes(**payload):
        raise RuntimeError("this extension is broken")

    def works(**payload):
        ran.append("second")

    bus.subscribe("broken", "message.received", explodes)
    bus.subscribe("fine", "message.received", works)

    succeeded = await bus.emit("message.received", text="hallo")

    assert succeeded == 1  # the working one still ran
    assert ran == ["second"]  # and it ran despite going second


async def test_both_sync_and_async_listeners_run() -> None:
    bus = HookBus()
    ran: list[str] = []

    async def slow(**payload):
        ran.append("async")

    bus.subscribe("a", "message.received", lambda **kw: ran.append("sync"))
    bus.subscribe("b", "message.received", slow)

    assert await bus.emit("message.received") == 2
    assert ran == ["sync", "async"]


async def test_cancellation_is_not_swallowed() -> None:
    """Cancellation is the caller going away, not the listener failing. Catching it
    here would keep a dying request alive."""
    import asyncio

    bus = HookBus()

    async def cancelled(**payload):
        raise asyncio.CancelledError

    bus.subscribe("x", "message.received", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await bus.emit("message.received")


def test_subscribing_to_an_unknown_event_is_refused() -> None:
    """A listener on a name nobody emits is silence, not a bug anybody finds."""
    bus = HookBus()

    with pytest.raises(UnknownEvent) as error:
        bus.subscribe("x", "message.recieved", lambda **kw: None)

    assert "message.recieved" in str(error.value)


def test_disabling_stops_an_extension_reacting(monkeypatch) -> None:
    registry = Registry()
    registry.load(
        _module(
            "ext_live", GOOD, lambda context: context.on("message.received", lambda **kw: None)
        )
    )

    assert registry.disable("telegram") is True
    assert registry.bus.listeners_for("message.received") == []
    assert registry.disable("telegram") is False  # already gone


# --- The core holds itself to the contract -----------------------------------


def test_the_built_in_extensions_all_load() -> None:
    """D-031: agent_core, database and web_chat are official applications, not
    privileged code paths - so they are parsed and validated like anything else."""
    registry = Registry()
    for path in BUILTIN:
        registry.load(path)

    assert sorted(registry.loaded) == [
        "agent_core",
        "database",
        "discord",
        "email",
        "instagram",
        "messenger",
        "slack",
        "telegram",
        "web_chat",
        "whatsapp",
    ]
    assert registry.failed == []


def test_web_chat_subscribes_to_incoming_messages() -> None:
    """D-027's mitigation: the contract is proven by use, not by inspection. Web chat
    is the first official application on it."""
    registry = Registry()
    for path in BUILTIN:
        registry.load(path)

    assert [entry.slug for entry in registry.bus.listeners_for("message.received")] == [
        "web_chat",
        "telegram",
        "email",
        "whatsapp",
        "messenger",
        "instagram",
        "discord",
        "slack",
    ]


def test_every_declared_hook_is_an_event_the_core_emits() -> None:
    """A built-in that subscribes to a name the core never emits would be dead code
    that looks alive."""
    registry = Registry()
    for path in BUILTIN:
        registry.load(path)

    for entry in registry.loaded.values():
        for hook in entry.manifest.hooks:
            assert hook in EVENTS, f"{entry.manifest.slug} declares unknown {hook!r}"


# --- The catalogue ------------------------------------------------------------


async def test_the_catalogue_records_what_is_loaded(migrated: AsyncSession) -> None:
    """The screen lists what is actually running, not what was installed once."""
    registry = Registry()
    for path in BUILTIN:
        registry.load(path)

    written = await sync_catalogue(migrated, registry)

    assert written == 10
    rows = {row.slug: row for row in (await migrated.execute(select(App))).scalars()}
    assert sorted(rows) == [
        "agent_core",
        "database",
        "discord",
        "email",
        "instagram",
        "messenger",
        "slack",
        "telegram",
        "web_chat",
        "whatsapp",
    ]
    assert rows["web_chat"].origin == "official"
    assert rows["web_chat"].manifest["hooks"] == ["message.received"]


async def test_syncing_twice_updates_rather_than_duplicates(
    migrated: AsyncSession,
) -> None:
    """Every restart runs this. A second row per app per boot would be a table that
    grows with uptime."""
    registry = Registry()
    for path in BUILTIN:
        registry.load(path)

    await sync_catalogue(migrated, registry)
    await sync_catalogue(migrated, registry)

    rows = (await migrated.execute(select(App))).scalars().all()
    assert len(rows) == 10
