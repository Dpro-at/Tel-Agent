"""Minting, resolving and rate-limiting the credentials the machine paths carry.

§B9.1 gives `POST /hooks/call` and the MCP endpoint each their own rotatable
credential, separate from the dashboard session. This module is that mechanism: the
minting, the lookup, the per-path ceilings, and the gate the authentication middleware
calls before it reaches the session check.

**Every refusal looks the same.** An unknown token, a revoked one, and a real token
presented at the wrong path all answer with one 401 and one message. Distinguishing
them would answer questions the caller has not earned: *wrong scope* tells whoever
holds a leaked MCP token that it is genuine, and that is the exact sentence §B9.1
exists to prevent — a leak of one must not open the others, and it must not confirm
itself either. `api.dependencies.Unauthenticated` makes the same choice for sessions.

**Two ceilings, because they stop different things** — the reasoning `security/quota.py`
sets out. One is per token, and it stops a misconfigured integration retrying in a
loop at the operator's expense. The other is per client address on requests that
presented *nothing valid*, and it stops a stranger working through the keyspace.
Guessing 256 bits is not a real threat; being able to try without limit is what makes
every later mistake in this file cheaper to exploit.
"""

from __future__ import annotations

import datetime as dt
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from starlette.requests import Request
from starlette.responses import Response

from api.errors import envelope_response
from api.models import MACHINE_SCOPES, MachineToken
from api.security.quota import Limit, consume
from api.security.session import hash_token

logger = logging.getLogger("api.machine")

# Which path family each scope opens. `/mcp` is matched exactly and as a prefix with a
# separator, so a future `/mcpanything` is not quietly inside it.
MACHINE_PREFIXES: dict[str, str] = {"/hooks/": "hooks", "/mcp/": "mcp"}
MACHINE_EXACT: dict[str, str] = {"/mcp": "mcp"}

# 32 bytes from the system CSPRNG, URL-safe, behind a scope-shaped label. The label is
# a convenience and never an authority: it makes a leaked token greppable in a log or a
# configuration file, and secret scanners can be taught the shape. What the token is
# actually good for is the `scope` column on its row.
_TOKEN_BYTES = 32
_PREFIX = "telagent"

# A provider posting call events, and a model calling tools. Both are machines, so the
# ceilings are far above any real integration and far below the rate at which a loop
# costs money. A misconfigured retry meets these; nothing working does.
PER_TOKEN: dict[str, Limit] = {
    "hooks": Limit(count=600, window=dt.timedelta(minutes=1)),
    "mcp": Limit(count=120, window=dt.timedelta(minutes=1)),
}

# What a caller may spend before proving anything at all.
PER_CLIENT = Limit(count=30, window=dt.timedelta(minutes=1))

# `last_used_at` is written at most this often, for the reason sessions give: every
# request would mean a write on every request, including the ones that only read.
LAST_USED_RESOLUTION = dt.timedelta(minutes=5)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite hands back naive datetimes; PostgreSQL hands back aware ones."""
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def scope_for(path: str) -> str | None:
    """The scope a path needs, or None when it is not a machine path at all."""
    if path in MACHINE_EXACT:
        return MACHINE_EXACT[path]
    for prefix, scope in MACHINE_PREFIXES.items():
        if path.startswith(prefix):
            return scope
    return None


def mint(scope: str) -> str:
    """A fresh token. Returned once, to the person who asked for it, and never again."""
    if scope not in MACHINE_SCOPES:
        raise ValueError(f"unknown scope {scope!r} - one of {sorted(MACHINE_SCOPES)}")
    return f"{_PREFIX}_{scope}_{secrets.token_urlsafe(_TOKEN_BYTES)}"


def bearer_from_request(request: Request) -> str | None:
    """The token out of `Authorization: Bearer …`, or nothing.

    Only that header. A credential in a query string is a credential in the access log
    of every proxy between the caller and here, and in the browser history if one is
    ever pointed at the URL.
    """
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def resolve(db: DbSession, presented: str | None, *, scope: str) -> MachineToken | None:
    """The live token for this value at this scope, or None.

    The scope is part of the query rather than checked afterwards, so there is no
    branch in which a row for the wrong path has been loaded and something later reads
    it.
    """
    if not presented:
        return None
    return await db.scalar(
        select(MachineToken).where(
            MachineToken.token_hash == hash_token(presented),
            MachineToken.scope == scope,
        )
    )


async def touch(db: DbSession, row: MachineToken) -> None:
    """Record that this credential was used, at most once every few minutes."""
    now = _now()
    if row.last_used_at is None or now - _aware(row.last_used_at) > LAST_USED_RESOLUTION:
        row.last_used_at = now
        await db.commit()


def _refused() -> Response:
    """The one refusal. See the module docstring: it never says which reason applied."""
    return envelope_response(
        status_code=401,
        code="machine_token_required",
        message="This path needs its own token, presented as `Authorization: Bearer …`.",
    )


def _too_many() -> Response:
    return envelope_response(
        status_code=429,
        code="rate_limited",
        message="Too many requests. Slow down and try again shortly.",
    )


async def guard(request: Request, db: DbSession, scope: str) -> Response | None:
    """Let a machine request through, or answer it. None means "carry on".

    Called by `AuthenticationMiddleware` before the session check, because these paths
    are not reachable with a session and a session is not reachable with these: the
    dashboard cookie is a different credential for a different door (§B9.1).
    """
    presented = bearer_from_request(request)
    row = await resolve(db, presented, scope=scope)

    if row is None:
        client = request.client.host if request.client else "unknown"
        allowed = await consume(db, f"machine:client:{client}", PER_CLIENT)
        # Committed even though the request is refused: the window it was refused in
        # has to survive, or the next attempt starts the count again.
        await db.commit()
        logger.info(
            "machine request refused",
            extra={"path": request.url.path, "scope": scope, "presented": bool(presented)},
        )
        return _too_many() if not allowed else _refused()

    if not await consume(db, f"machine:token:{row.id}", PER_TOKEN[scope]):
        await db.commit()
        logger.warning("machine token %s is over its ceiling", row.id)
        return _too_many()
    await db.commit()

    await touch(db, row)
    request.state.machine_token = row
    request.state.workspace_id = row.workspace_id
    return None
