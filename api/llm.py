"""Which model this installation answers with, and where that was decided.

§B9.2 puts a user-entered provider key in an encrypted column and the key that encrypts
it in `.env`. This module is the reader that turns those rows back into the thing the
agent understands, and it is on the `api/` side of the boundary because `agent/` may not
import `api/` and the store lives here.

**The store first, the environment second — per value.** That order is the point of P3:
`.env` is what an installer wrote once, the store is what the owner changed from the
settings screen afterwards, and a value set on a screen that lost to a stale environment
variable would be a setting that appears to save and does nothing. Per *value* rather
than per source, so an installation already running on `.env` can move one field at a
time: type a new model name, save, and the key that is already working keeps working.

**Read on every turn, and never cached.** §B9.2's table says a credential entered from
the UI takes effect immediately, and a cache is how that becomes "after the next
restart". The cost is one indexed read per reply, against a model call that is four
orders of magnitude slower.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agent.config import ConfigurationError, LlmSettings, environment_values, settings_from
from agent.providers.llm import LLMProvider, provider_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession as DbSession

logger = logging.getLogger("api.llm")

# The keys, and what to call them when one of them is missing. A person who typed a key
# into a form cannot act on the words `LLM_API_KEY`, so the refusal names the field they
# are looking at instead - `agent.config.ENVIRONMENT_NAMES` is the same idea for the
# other source.
KEYS = {
    "provider": "llm.provider",
    "model": "llm.model",
    "api_key": "llm.api_key",
    "base_url": "llm.base_url",
}

# Said the same way wherever it is reported: the health row and the test button are
# both diagnosis surfaces, and two wordings for one fault read as two faults.
UNREADABLE_KEY = (
    "The stored key cannot be decrypted. ENCRYPTION_KEY has changed since it was "
    "saved - restore the old one, or save the key again."
)

SCREEN_NAMES = {
    "provider": "the provider",
    "model": "the model name",
    "api_key": "the API key",
}


async def stored_values(db: DbSession) -> dict[str, str]:
    """The four values as the store holds them, unmasked, empty string for unset.

    Unmasked on purpose and only here: `store.all_for` is what a screen reads and it
    masks, `store.get` is what a caller about to *use* a credential reads. This is that
    caller.
    """
    from api.settings import store

    return {field: str(await store.get(db, key) or "") for field, key in KEYS.items()}


async def resolve(db: DbSession) -> LlmSettings | None:
    """The model this installation will actually use, or `None` when it has none.

    Raises `ConfigurationError` for half a configuration - a provider named with
    nothing behind it. The caller decides what to do with that: the health screen
    shows it as the reason a row is red, and a conversation lets it raise, because an
    installation that thinks it has a model and does not must not answer as though it
    never had one.
    """
    stored = await stored_values(db)
    environment = environment_values()
    merged = {
        field: stored[field] or environment[field]
        for field in ("provider", "model", "api_key", "base_url")
    }
    return settings_from(**merged, names=SCREEN_NAMES)


async def resolve_provider(db: DbSession) -> LLMProvider | None:
    """`resolve`, as the object that streams. `None` when no model is configured."""
    settings = await resolve(db)
    return None if settings is None else provider_for(settings)


async def describe(db: DbSession) -> tuple[str, str | None]:
    """A state and a detail for the health screen, without spending a model request.

    A health screen that calls the model every time it is opened is a health screen
    with a bill, and what an owner needs from this row - whether this installation has
    a model at all - is answerable without one. A model that is configured and refusing
    shows up where it matters, in a conversation, and lands in the tray.
    """
    from api.security.crypto import DecryptionFailed

    try:
        settings = await resolve(db)
    except ConfigurationError as broken:
        # Half a configuration is worse than none: the agent refuses to answer and the
        # owner has no way to see why from the outside. This row is that way.
        return "down", str(broken)
    except DecryptionFailed:
        # The stored key cannot be opened - ENCRYPTION_KEY was rotated or lost, and
        # every credential on this installation is in the same state. Answered rather
        # than raised, because the health screen is exactly where somebody goes when
        # credentials stop working, and a 500 there is the least useful moment for one.
        # The reply path still raises: a turn that cannot read its key must not answer
        # as though no model were configured.
        logger.exception("the stored model key could not be decrypted")
        return "down", UNREADABLE_KEY
    if settings is None:
        return "not_configured", None
    # The model and where it lives. Never the key - this endpoint is admin-only, and
    # that is not a reason to hand one back.
    return "ok", f"{settings.model} at {settings.base_url}"
