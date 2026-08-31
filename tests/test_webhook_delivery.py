"""Sending a webhook, and proving it came from here — G5.

`api/routes/webhooks.py` said it at the top since it was written: *"Nothing sends
anything yet."* These are about the part that sends.

The one that matters most is the last: the signature is over the bytes that were
delivered. A sender that serialises twice produces a body the receiver cannot verify,
and the failure is invisible from both ends — the code looks right, the JSON looks
right, and the hash does not match.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api import webhooks
from api.models import BackgroundJob, Webhook, Workspace

SECRET = "the-shared-secret-a-receiver-also-has"  # noqa: S105
NOW = dt.datetime(2026, 8, 31, 10, 0, tzinfo=dt.UTC)
KEY_HEX = "aa" * 32


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch):
    """A webhook's secret is an encrypted column, so these tests need a key.

    Autouse and not optional: without it every test that writes a `Webhook` fails with
    `EncryptionKeyError`, and on a machine that happens to have a real `ENCRYPTION_KEY`
    in `.env` it passes anyway - which is how this reached CI green locally and red
    there.
    """
    from api.config import get_settings
    from api.models.encrypted import reset_key_cache

    monkeypatch.setenv("ENCRYPTION_KEY", KEY_HEX)
    get_settings.cache_clear()
    reset_key_cache()
    yield
    get_settings.cache_clear()
    reset_key_cache()


@pytest.fixture
async def workspace(migrated: AsyncSession) -> int:
    row = Workspace(name="Wagner & Partner")
    migrated.add(row)
    await migrated.commit()
    return row.id


async def _hook(
    db: AsyncSession, workspace_id: int, *, events: list[str], enabled: bool = True
) -> Webhook:
    row = Webhook(
        workspace_id=workspace_id,
        url="https://wagner-partner.test/hooks/tel-agent",
        events=events,
        secret=SECRET,
        enabled=enabled,
    )
    db.add(row)
    await db.commit()
    return row


# --- The signature ------------------------------------------------------------


def test_the_timestamp_is_inside_what_is_signed() -> None:
    """Signing the body alone leaves a delivery replayable for the life of the secret:
    the capture is still valid because the body has not changed."""
    body = b'{"event":"x"}'

    assert webhooks.sign(SECRET, timestamp=1, body=body) != webhooks.sign(
        SECRET, timestamp=2, body=body
    )


def test_the_recipe_in_the_specification_verifies_what_the_sender_produces() -> None:
    """The documented recipe, written out by hand rather than by calling `sign`.

    If this ever fails, either the sender changed or `docs/SPEC.md` did, and a receiver
    that followed the published instructions is rejecting real deliveries.
    """
    body = webhooks.envelope("message.received", {"text": "hallo"}, sent_at=NOW)
    timestamp = int(NOW.timestamp())

    expected = (
        "sha256="
        + hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    )

    assert webhooks.sign(SECRET, timestamp=timestamp, body=body) == expected


def test_the_envelope_keeps_the_payload_under_data() -> None:
    """So the day a field is added beside `event` there is somewhere to put it that
    does not move what every receiver is already reading."""
    body = json.loads(webhooks.envelope("message.received", {"text": "hallo"}, sent_at=NOW))

    assert set(body) == {"event", "sent_at", "data"}
    assert body["data"] == {"text": "hallo"}


# --- Who gets told ------------------------------------------------------------


async def test_only_the_hooks_that_asked_for_this_event(
    migrated: AsyncSession, workspace: int
) -> None:
    await _hook(migrated, workspace, events=["message.received"])
    await _hook(migrated, workspace, events=["conversation.ended"])

    queued = await webhooks.queue(
        migrated, workspace_id=workspace, event="message.received", data={}
    )
    await migrated.commit()

    assert queued == 1


async def test_a_switched_off_hook_is_not_queued(
    migrated: AsyncSession, workspace: int
) -> None:
    await _hook(migrated, workspace, events=["message.received"], enabled=False)

    assert (
        await webhooks.queue(
            migrated, workspace_id=workspace, event="message.received", data={}
        )
        == 0
    )


async def test_another_workspaces_hook_never_hears_about_this_one(
    migrated: AsyncSession, workspace: int
) -> None:
    """The isolation that matters most here: a webhook is an export, so one business's
    conversation reaching another's endpoint is the worst thing this can do."""
    theirs = Workspace(name="Wolf Studio")
    migrated.add(theirs)
    await migrated.flush()
    await _hook(migrated, theirs.id, events=["message.received"])

    assert (
        await webhooks.queue(
            migrated, workspace_id=workspace, event="message.received", data={}
        )
        == 0
    )


async def test_the_secret_is_not_written_into_the_queue(
    migrated: AsyncSession, workspace: int
) -> None:
    """A queued row is readable by anything that can read the database, and it is a
    strange second home for a credential. The handler reads the live one, which is also
    what makes rotating a secret rescue the deliveries already waiting.
    """
    await _hook(migrated, workspace, events=["message.received"])
    await webhooks.queue(
        migrated, workspace_id=workspace, event="message.received", data={"text": "hallo"}
    )
    await migrated.commit()

    jobs = (await migrated.scalars(select(BackgroundJob))).all()
    assert jobs, "nothing was queued, so this proves nothing"
    assert SECRET not in json.dumps([job.payload for job in jobs], default=str)
    # And what it does carry is the id, so the handler can read the live secret.
    assert jobs[0].payload["webhook_id"]


# --- The delivery -------------------------------------------------------------


async def test_the_signature_covers_the_bytes_that_were_delivered(
    migrated: AsyncSession, workspace: int
) -> None:
    """The failure this prevents is invisible from both ends.

    Serialise once for the signature and again for the body and the two can differ in
    key order or spacing. The code looks right, the JSON looks right, and every
    delivery is rejected by a receiver following the published recipe.
    """
    hook = await _hook(migrated, workspace, events=["message.received"])
    seen: dict[str, object] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        seen["headers"] = dict(request.headers)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await webhooks.send(
            hook,
            event="message.received",
            data={"text": "hallo"},
            delivery_id=7,
            now=NOW,
            client=client,
        )

    body = seen["body"]
    headers = seen["headers"]
    timestamp = int(headers["x-tel-agent-timestamp"])

    # Verified exactly as a receiver would, against the bytes that arrived.
    expected = (
        "sha256="
        + hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    )
    assert headers["x-tel-agent-signature"] == expected
    assert headers["x-tel-agent-event"] == "message.received"
    assert headers["x-tel-agent-delivery"] == "7"


async def test_a_refusal_is_raised_so_the_runner_retries(
    migrated: AsyncSession, workspace: int
) -> None:
    """The runner already knows how many attempts this has had; this function does not,
    so it reports and lets that decision be made where the count lives."""
    hook = await _hook(migrated, workspace, events=["message.received"])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(500))
    ) as client:
        with pytest.raises(RuntimeError) as failed:
            await webhooks.send(
                hook, event="message.received", data={}, delivery_id=1, now=NOW, client=client
            )

    assert "500" in str(failed.value)


async def test_a_redirect_is_a_failure_rather_than_somewhere_to_follow(
    migrated: AsyncSession, workspace: int
) -> None:
    """A signed POST that follows a redirect delivers the signature to a host the
    operator never registered."""
    hook = await _hook(migrated, workspace, events=["message.received"])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(302, headers={"Location": "https://elsewhere.test/"})
        )
    ) as client:
        with pytest.raises(RuntimeError):
            await webhooks.send(
                hook, event="message.received", data={}, delivery_id=1, now=NOW, client=client
            )
