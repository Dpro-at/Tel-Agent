"""Authentication for WebSocket connections — D15.

An unauthenticated socket is a worse hole than an unauthenticated endpoint, because it
stays open: one missed check and somebody is streaming a live conversation for as long
as they care to listen. Two pieces close it:

* **The gate** (`WebSocketAuthMiddleware` in `api/middleware/ws_auth.py`) refuses the
  upgrade *before* the socket is accepted, applying the same closed-by-default rule the
  HTTP side already lives under. `BaseHTTPMiddleware` never sees websocket scopes, so
  without this every future websocket route would silently bypass authentication.

* **The watchdog** (`watch_session` here) closes an *open* socket when its session is
  deleted, rather than at the next message. "Sign out everywhere" has to mean the
  forgotten tablet's live view goes dark now — a socket that only notices at its next
  message may never notice at all, because a listener sends nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from api.models import Session
from api.security.session import hash_token

if TYPE_CHECKING:
    from fastapi import WebSocket
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = logging.getLogger("api.auth")

# Policy violation - the standard close code for "you are not allowed to be here".
WS_POLICY_VIOLATION = 1008

# How often the watchdog re-checks the session row. Five seconds keeps "sign out
# everywhere" feeling immediate without turning every open socket into a query storm.
WATCH_INTERVAL_SECONDS = 5.0


async def watch_session(
    websocket: WebSocket,
    sessionmaker: async_sessionmaker,
    token: str,
    *,
    interval: float = WATCH_INTERVAL_SECONDS,
) -> None:
    """Close the socket the moment its session stops existing.

    Run as a background task alongside the route's own send/receive loop:

        watchdog = asyncio.create_task(
            watch_session(websocket, app.state.sessionmaker, token)
        )
        try:
            ...  # the route's actual work
        finally:
            watchdog.cancel()

    A fresh short-lived database session per check, not a held one: this loop lives as
    long as the socket does, and parking a pooled connection under every open socket
    would drain the pool with idle holders.
    """
    token_hash = hash_token(token)
    try:
        while True:
            await asyncio.sleep(interval)
            async with sessionmaker() as db:
                row = await db.scalar(
                    select(Session.id).where(Session.token_hash == token_hash)
                )
            if row is None:
                logger.info("session revoked; closing websocket")
                with contextlib.suppress(Exception):
                    await websocket.close(code=WS_POLICY_VIOLATION)
                return
    except asyncio.CancelledError:
        # The route finished on its own; nothing to clean up.
        raise
