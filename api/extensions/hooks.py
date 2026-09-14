"""The event bus extensions listen on.

The core emits; extensions react. The core never imports an extension, and an extension
never calls into a route — the event name is the whole of the coupling between them,
which is what lets a channel be written by somebody we will never meet.

**A failing hook must not break what emitted the event.** A message arrives, is stored,
and the arrival is announced; if a Telegram extension throws while reacting, the message
is still stored. The alternative — letting the exception travel back up — makes every
extension a way to break the core, which in an in-process design would be the whole
product's stability handed to whoever wrote the worst plugin.

So `emit` isolates each listener: the failure is logged with the extension's slug, and
the remaining listeners still run. What it deliberately does **not** do is retry or
queue: a hook is a reaction, and a reaction that must not be lost belongs in the jobs
table where it can be retried with a record, not in a fire-and-forget bus.

**Per-workspace filtering (#242).** An app switched off under the Apps screen in one
workspace must not receive events for that workspace, but must still receive events for
workspaces where it is enabled. `HookBus` carries a cache of which slugs are enabled per
workspace (`_enabled`) and consults it in `emit` when `workspace_id` is given. The cache
is updated in `PUT /api/apps/{slug}` — the only endpoint that changes `enabled` — so the
hot path (message received, send to N listeners) costs zero database queries.

Slugs absent from the cache are treated as enabled: a bus with an empty cache behaves
exactly as it did before the filter was introduced, and callers that do not yet pass a
workspace_id are not penalised.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("api.extensions")

# Events the core promises to emit. Closed on purpose: a listener subscribing to a
# name nobody emits is a silence that looks like a bug for a very long time, so
# subscribing to an unknown event is refused at registration.
EVENTS = (
    "conversation.started",
    "conversation.ended",
    "message.received",
    "message.sent",
    "channel.connected",
    "channel.failed",
    "notification.raised",
    "app.installed",
    "app.enabled",
    "app.disabled",
)


class UnknownEvent(ValueError):
    """A listener asked for an event the core does not emit."""

    def __init__(self, event: str) -> None:
        super().__init__(
            f"{event!r} is not an event this core emits. Known events: {list(EVENTS)}. "
            "Add it to EVENTS in api/extensions/hooks.py and emit it, or subscribe to "
            "one that exists - a listener on a name nobody emits is silence, not a bug "
            "anybody will find."
        )


@dataclass
class Listener:
    slug: str
    event: str
    handler: Callable[..., Any]


@dataclass
class HookBus:
    """Listeners, grouped by event, filtered per workspace.

    An instance rather than a module global: tests build one per case, and a future
    per-workspace bus is then a change of ownership rather than a rewrite.

    The `_enabled` cache maps workspace_id → set of slugs that are enabled in that
    workspace. A slug that is absent from the set for a given workspace is treated as
    disabled and its listeners are skipped when `workspace_id` is supplied to `emit`.
    A workspace_id that is absent from `_enabled` entirely means no filter has been
    populated for it yet — all slugs are considered enabled, which preserves the
    behaviour callers that do not pass workspace_id already receive.
    """

    listeners: dict[str, list[Listener]] = field(default_factory=dict)
    # workspace_id → set of slugs currently enabled in that workspace.
    # Populated and maintained by set_workspace_enabled(); never queried from the
    # database, so this dict is the only cost on the hot path.
    _enabled: dict[int, set[str]] = field(default_factory=dict)

    def subscribe(self, slug: str, event: str, handler: Callable[..., Any]) -> None:
        if event not in EVENTS:
            raise UnknownEvent(event)
        self.listeners.setdefault(event, []).append(Listener(slug, event, handler))

    def unsubscribe_all(self, slug: str) -> int:
        """Remove every listener an extension registered — disabling it, in effect."""
        removed = 0
        for event, entries in self.listeners.items():
            keep = [entry for entry in entries if entry.slug != slug]
            removed += len(entries) - len(keep)
            self.listeners[event] = keep
        return removed

    def listeners_for(self, event: str) -> list[Listener]:
        return list(self.listeners.get(event, []))

    def set_workspace_enabled(self, workspace_id: int, slug: str, *, enabled: bool) -> None:
        """Record that an app was switched on or off in one workspace.

        Called from PUT /api/apps/{slug} — the only writer of AppInstall.enabled —
        immediately after the database commit, so the cache and the table stay in step
        without a read-on-every-emit.
        """
        slugs = self._enabled.setdefault(workspace_id, set())
        if enabled:
            slugs.add(slug)
        else:
            slugs.discard(slug)

    def is_enabled_for(self, workspace_id: int, slug: str) -> bool:
        """Whether this slug's hooks should fire for the given workspace.

        Returns True when no entry exists for the workspace — an empty cache means the
        filter has not been populated and the bus behaves as if everything is on.
        """
        if workspace_id not in self._enabled:
            return True
        return slug in self._enabled[workspace_id]

    async def emit(self, event: str, *, workspace_id: int | None = None, **payload: Any) -> int:
        """Announce something happened. Returns how many listeners ran without raising.

        When `workspace_id` is given, listeners whose slug is switched off in that
        workspace are skipped — no database query, just a set lookup in `_enabled`.

        Listeners run in registration order, one after another rather than gathered:
        two extensions reacting to the same message often both want the database
        session in the payload, and a session is not safe to use concurrently.
        """
        if event not in EVENTS:
            raise UnknownEvent(event)

        succeeded = 0
        for listener in self.listeners.get(event, []):
            if workspace_id is not None and not self.is_enabled_for(
                workspace_id, listener.slug
            ):
                continue
            try:
                result = listener.handler(**payload)
                if inspect.isawaitable(result):
                    await result
                succeeded += 1
            except asyncio.CancelledError:
                # Cancellation is the caller going away, not the listener failing.
                # Swallowing it here would keep a dying request alive.
                raise
            except Exception:
                logger.exception(
                    "extension hook failed",
                    extra={"app": listener.slug, "event": event},
                )
        return succeeded
