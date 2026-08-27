"""Reading and writing settings — the store the eleven-tab screen will use.

Every function refuses a key that is not declared in the registry, so a typo fails at
the call that made it rather than becoming a row nothing ever reads.

**Resolution order for a workspace-scoped key:** the workspace's own row, then the
installation row, then the declared default. That is what lets a settings screen offer
"use the default" as a real state — clearing a workspace value falls back rather than
storing an empty string, which would be a value that happens to look absent.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Setting
from api.security.crypto import mask
from api.settings.registry import REGISTRY, Definition, coerce, definition, serialise

logger = logging.getLogger("api.settings")


def _stored(row: Setting, spec: Definition) -> str | None:
    return row.secret_value if spec.secret else row.value


async def _row(db: DbSession, key: str, workspace_id: int | None) -> Setting | None:
    return await db.scalar(
        select(Setting).where(
            Setting.key == key,
            Setting.workspace_id.is_(None)
            if workspace_id is None
            else Setting.workspace_id == workspace_id,
        )
    )


async def get(db: DbSession, key: str, *, workspace_id: int | None = None) -> Any:
    """The effective value: workspace, then installation, then the default."""
    spec = definition(key)

    if workspace_id is not None and spec.scope == "workspace":
        row = await _row(db, key, workspace_id)
        raw = _stored(row, spec) if row else None
        if raw is not None:
            return coerce(spec, raw)

    row = await _row(db, key, None)
    raw = _stored(row, spec) if row else None
    if raw is not None:
        return coerce(spec, raw)

    return spec.default


async def set_value(
    db: DbSession, key: str, value: Any, *, workspace_id: int | None = None
) -> None:
    """Store a value. The caller commits.

    A workspace-scoped write with no workspace stores the installation-wide default for
    that key, which is exactly what an administrator setting a policy for everyone
    means.
    """
    spec = definition(key)
    if spec.scope == "installation" and workspace_id is not None:
        raise ValueError(f"{key!r} is an installation setting; it has no per-workspace value.")

    row = await _row(db, key, workspace_id)
    if row is None:
        row = Setting(key=key, workspace_id=workspace_id)
        db.add(row)

    raw = serialise(spec, value)
    if spec.secret:
        row.secret_value, row.value = raw, None
    else:
        row.value, row.secret_value = raw, None
    row.updated_at = dt.datetime.now(dt.UTC)

    # The value itself is never logged, secret or not: "not marked secret" is a
    # judgement about masking in the UI, not a promise that it is safe in a log file.
    logger.info("setting written", extra={"key": key, "workspace_id": workspace_id})


async def clear(db: DbSession, key: str, *, workspace_id: int | None = None) -> None:
    """Remove an override so the value falls back. The caller commits."""
    definition(key)  # refuses unknown keys the same way
    row = await _row(db, key, workspace_id)
    if row is not None:
        await db.delete(row)


async def all_for(
    db: DbSession, *, workspace_id: int | None = None, reveal: bool = False
) -> dict[str, Any]:
    """Every declared setting with its effective value, for a settings screen.

    Secrets come back masked. `reveal` exists for the one legitimate reader — the code
    that is about to use a credential — and is never reachable from a route: §B9
    requires that a saved key is never returned in full to a client, and an endpoint
    with a `reveal` flag is that requirement one query parameter away from being
    forgotten.
    """
    result: dict[str, Any] = {}
    for key, spec in REGISTRY.items():
        if spec.scope == "workspace" and workspace_id is None:
            # Ask for the installation-wide default of a per-workspace key and you get
            # exactly that; the workspace's own value needs a workspace to ask about.
            pass
        value = await get(db, key, workspace_id=workspace_id)
        if spec.secret and value and not reveal:
            value = mask(str(value))
        result[key] = value
    return result
