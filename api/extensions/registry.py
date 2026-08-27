"""Loading extensions, and keeping a bad one from taking the server with it.

An extension is a Python module exposing two names:

    MANIFEST = {...}                 # the declaration, validated by manifest.parse
    def register(context): ...       # called once at load; subscribes to hooks

`register` receives a `Context` and nothing else. It is the only handle an extension is
given, so what an extension can do *conveniently* is visible in one place — and in an
in-process design, convenience is most of what governs behaviour, since nothing stops a
determined module from importing whatever it likes.

**Loading is the one place a bad extension is cheap to survive.** A module that raises
on import, or whose manifest is malformed, or whose `register` throws, is recorded as
failed and skipped; the others still load and the server still starts. That is the whole
mitigation the in-process decision leaves available at load time, and it is applied
without exception — including to official extensions, because "ours cannot be broken"
is how a broken one ships.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any

from api.extensions.hooks import HookBus
from api.extensions.manifest import InvalidManifest, Manifest, parse

logger = logging.getLogger("api.extensions")


@dataclass
class Context:
    """What an extension is handed at registration.

    `on` is the subscription function, already bound to the extension's slug so a
    listener is always attributable to whoever registered it — an anonymous failing
    hook is a failure nobody can switch off.
    """

    slug: str
    manifest: Manifest
    bus: HookBus

    def on(self, event: str, handler: Any) -> None:
        self.bus.subscribe(self.slug, event, handler)

    def may(self, scope: str) -> bool:
        """Did this extension declare the scope it is about to use?

        Advisory in this runtime, and the docstring on `manifest.py` says why. Code that
        checks it is code that keeps working unchanged when the boundary becomes real.
        """
        return scope in self.manifest.scopes


@dataclass
class Loaded:
    manifest: Manifest
    module: Any


@dataclass
class Failed:
    slug: str
    reason: str


@dataclass
class Registry:
    """Everything this process has loaded, and everything it refused."""

    bus: HookBus = field(default_factory=HookBus)
    loaded: dict[str, Loaded] = field(default_factory=dict)
    failed: list[Failed] = field(default_factory=list)

    def load(self, module_path: str) -> Loaded | None:
        """Import one extension and register it. Returns None if it was refused.

        Nothing here raises. The caller is application startup, and a startup that dies
        because one community extension has a typo is a product that cannot be extended
        by anybody but us.
        """
        try:
            module = importlib.import_module(module_path)
        except Exception as error:
            return self._refuse(module_path, f"import failed: {error!r}")

        raw = getattr(module, "MANIFEST", None)
        if not isinstance(raw, dict):
            return self._refuse(module_path, "no MANIFEST dict")

        try:
            manifest = parse(raw)
        except InvalidManifest as error:
            return self._refuse(module_path, str(error))

        if manifest.slug in self.loaded:
            return self._refuse(
                manifest.slug,
                f"slug already loaded from {self.loaded[manifest.slug].module.__name__}",
            )

        register = getattr(module, "register", None)
        if not callable(register):
            return self._refuse(manifest.slug, "no register() function")

        # Declared hooks and actual subscriptions are cross-checked below, so a
        # manifest that lies about what it listens to is caught rather than believed.
        try:
            register(Context(slug=manifest.slug, manifest=manifest, bus=self.bus))
        except Exception as error:
            self.bus.unsubscribe_all(manifest.slug)
            return self._refuse(manifest.slug, f"register() raised: {error!r}")

        subscribed = {
            listener.event
            for event in self.bus.listeners
            for listener in self.bus.listeners_for(event)
            if listener.slug == manifest.slug
        }
        undeclared = subscribed - set(manifest.hooks)
        if undeclared:
            self.bus.unsubscribe_all(manifest.slug)
            return self._refuse(
                manifest.slug,
                f"subscribed to undeclared event(s) {sorted(undeclared)} - the manifest "
                "is what the catalogue shows, so it has to be true",
            )

        entry = Loaded(manifest=manifest, module=module)
        self.loaded[manifest.slug] = entry
        logger.info(
            "extension loaded",
            extra={
                "app": manifest.slug,
                "version": manifest.version,
                "origin": manifest.origin,
                "hooks": list(manifest.hooks),
            },
        )
        return entry

    def disable(self, slug: str) -> bool:
        """Stop an extension reacting, without restarting the process."""
        if slug not in self.loaded:
            return False
        self.bus.unsubscribe_all(slug)
        del self.loaded[slug]
        logger.info("extension disabled", extra={"app": slug})
        return True

    def _refuse(self, slug: str, reason: str) -> None:
        self.failed.append(Failed(slug=slug, reason=reason))
        logger.error("extension refused", extra={"app": slug, "reason": reason})
        return None


async def sync_catalogue(db: Any, registry: Registry) -> int:
    """Write what is loaded into the `apps` table so the catalogue can list it.

    The table is the record; the registry is this process's live view of it. Keeping
    them in step here means the catalogue screen shows what is actually running rather
    than what was installed at some point in the past.
    """
    from sqlalchemy import select

    from api.models import App

    written = 0
    for slug, entry in registry.loaded.items():
        row = await db.scalar(select(App).where(App.slug == slug))
        if row is None:
            row = App(slug=slug)
            db.add(row)
        row.origin = entry.manifest.origin
        row.version = entry.manifest.version
        row.manifest = entry.manifest.as_json()
        written += 1
    await db.commit()
    return written
