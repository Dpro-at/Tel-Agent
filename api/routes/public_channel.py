"""The public door, for every channel that receives webhooks.

One address family, `/public/{kind}/{path}`, and one rule above all the others: **one
refusal for every reason.** An unknown address, a kind nothing implements, a channel
that is switched off, a body that does not verify — all of them answer 403
`not_recognised`, exactly as `api/routes/meta_chat_channels.py` does. The door tells a
stranger nothing about what is behind it, including whether anything is.

What it does *not* do is verify the platform's signature. That belongs to the channel,
because every platform proves itself differently, and `module.receive` is where the
raw body is still raw. This file finds the channel and hands it over.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.channels import generic
from api.errors import envelope_response
from api.models import Channel

logger = logging.getLogger("api.public_channel")

router = APIRouter(tags=["channels"])


def _refused() -> object:
    return envelope_response(
        status_code=status.HTTP_403_FORBIDDEN,
        code="not_recognised",
        message="This address did not accept the request.",
    )


@router.api_route("/public/{kind}/{path}", methods=["GET", "POST"], include_in_schema=False)
async def door(request: Request, kind: str, path: str) -> object:
    """One delivery, or one verification handshake, for one channel.

    A GET is how most platforms prove the address before they ever send to it, and it
    reaches the same `receive` — the channel knows which of the two it is looking at,
    and the door would only have to guess.
    """
    module = generic.module_for(kind)
    if module is None or getattr(module, "INBOUND", "") != "door":
        logger.info("public door refused", extra={"reason": "no such door", "kind": kind})
        return _refused()

    db: DbSession = request.state.db
    channel = await db.scalar(
        select(Channel).where(
            Channel.webhook_path == path,
            Channel.kind == kind,
            Channel.status == "active",
        )
    )
    if channel is None:
        logger.info("public door refused", extra={"reason": "no such channel", "kind": kind})
        return _refused()

    return await module.receive(db, channel, request)
