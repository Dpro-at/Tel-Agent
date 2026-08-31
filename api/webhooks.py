"""Sending a webhook, and proving it came from here — G5.

`api/routes/webhooks.py` has held the registry since it was written, and said so at the
top: *"Nothing sends anything yet."* This is the part that sends.

**Why a signature at all.** A webhook is a POST from an unauthenticated stranger as far
as the receiver is concerned. Anybody who learns the URL — and a URL leaks through
proxy logs, browser history, a screenshot in a ticket — can post whatever they like to
it, and a receiver that acts on `conversation.ended` will act on a forged one. The
shared secret is what makes the receiver able to tell.

**The timestamp is inside the signed string, not beside it.** Signing only the body
means a message captured once can be replayed for as long as the secret lives, and the
signature stays valid because the body has not changed. Signing `timestamp.body` means
the receiver can refuse anything older than a few minutes and know the timestamp was
not edited on the way.

**The envelope has a `data` key from the first delivery.** Not decoration: everything
this ever sends is under it, so the day a field has to be added beside `event` there is
somewhere to put it that does not move what a receiver is already reading. A payload
whose top level is the data itself has no such place, and gains one only by breaking
every receiver at once.

**Delivery, retries and permanent failure are the background runner's**, which already
does all three (`api/jobs/runner.py`). A hook that is briefly unreachable produces a
webhook that arrives late rather than one that is lost, and the operator sees the
failure on a job rather than in a log nobody reads.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from api.models import Webhook

logger = logging.getLogger("api.webhooks")

# The receiver reads these. Named rather than inlined so the documented verification
# recipe and the sender cannot drift apart.
EVENT_HEADER = "X-Tel-Agent-Event"
TIMESTAMP_HEADER = "X-Tel-Agent-Timestamp"
SIGNATURE_HEADER = "X-Tel-Agent-Signature"
# The job's id. A receiver that acts on a delivery keeps this and ignores a repeat:
# retries mean the same event can arrive twice, and "at least once" is the only
# promise a sender that retries can honestly make.
DELIVERY_HEADER = "X-Tel-Agent-Delivery"

# Prefixed with the algorithm so that adding a second one later is a new prefix rather
# than a guess about which of two hashes a hex string is.
SIGNATURE_PREFIX = "sha256="

# Tight on purpose. A receiver that takes longer than this is a receiver whose reply
# nobody is waiting for, and the runner will try again - holding a worker open for a
# slow endpoint is how one bad receiver stops every other hook.
TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


def sign(secret: str, *, timestamp: int, body: bytes) -> str:
    """The value of the signature header for this body at this moment.

    HMAC-SHA256 over `{timestamp}.{body}`, hex, behind its algorithm's name. The
    documented recipe in `docs/SPEC.md` is this function in words; if one changes the
    other has to.
    """
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def envelope(event: str, data: dict[str, Any], *, sent_at: dt.datetime) -> bytes:
    """The body, as bytes, because bytes are what gets signed.

    Serialised once and both signed and sent, never serialised twice: two dumps of one
    dictionary can differ in key order or spacing, and a signature over a different
    byte string than the one delivered fails at the receiver for a reason nobody can
    see from either end.
    """
    return json.dumps(
        {"event": event, "sent_at": sent_at.isoformat(), "data": data},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


async def queue(db: DbSession, *, workspace_id: int, event: str, data: dict[str, Any]) -> int:
    """Hand this event to every hook in the workspace that asked for it.

    Returns how many were queued. Nothing is sent here: this runs inside a request, and
    a request that waits for somebody else's server is a request that inherits their
    outage. The caller commits.
    """
    from api.jobs.runner import enqueue

    rows = (
        (
            await db.execute(
                select(Webhook).where(
                    Webhook.workspace_id == workspace_id, Webhook.enabled.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )

    queued = 0
    for hook in rows:
        if event not in (hook.events or []):
            continue
        # The webhook's id, not its secret. A payload row is readable by anything that
        # can read the database, and a queue is a strange second place for a credential
        # to live; the handler reads the live one at send time, which also means
        # rotating a secret rescues the deliveries already waiting.
        await enqueue(db, "webhook", {"webhook_id": hook.id, "event": event, "data": data})
        queued += 1

    if queued:
        logger.info("webhooks queued", extra={"event": event, "count": queued})
    return queued


async def send(
    hook: Webhook,
    *,
    event: str,
    data: dict[str, Any],
    delivery_id: int,
    now: dt.datetime,
    client: httpx.AsyncClient | None = None,
) -> None:
    """POST one signed delivery. Raises on anything that is worth retrying.

    A 2xx is success. Everything else raises, and the runner decides whether that
    becomes a retry or a permanent failure - it already knows how many attempts this
    delivery has had and this function does not.
    """
    body = envelope(event, data, sent_at=now)
    timestamp = int(now.timestamp())
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: event,
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(hook.secret, timestamp=timestamp, body=body),
        DELIVERY_HEADER: str(delivery_id),
    }

    async def post(http: httpx.AsyncClient) -> httpx.Response:
        return await http.post(hook.url, content=body, headers=headers)

    if client is not None:
        response = await post(client)
    else:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as http:
            response = await post(http)

    if response.status_code >= 300:
        # Redirects included, deliberately. A signed POST that follows a redirect
        # delivers the signature somewhere the operator never registered, and that is
        # the one failure mode worth being noisy about rather than quietly obeying.
        raise RuntimeError(f"{hook.url} answered {response.status_code}")
