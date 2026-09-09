"""SMS — a text to a number the business already owns, §B13.

The first channel built on the declarative contract (D-044): no routes of its own, a
`Setup` beside the transport, and the generic card and door serving both. The customer
pastes the credentials of **their own** messaging account; Tel-Agent holds no shared
application here any more than it does anywhere else.

**Inbound is a door.** The platform posts a form body to `/public/sms/{webhook_path}`
and waits for an empty TwiML document. That answer has to be immediate: the platform
gives a webhook about fifteen seconds and then retries, so generating the reply inside
the request would earn the customer two identical texts. The door verifies, stores,
acknowledges, and answers from a task afterwards — the same acknowledge-first split
every webhook channel in this product makes.

**The signature is over the address, not only the body.** The platform signs the exact
public URL it called plus every POST parameter, key and value concatenated in key
order, HMAC-SHA1 under the account's auth token. Including the URL is what stops a
delivery signed for one installation from being replayed at another, so behind a proxy
the forwarded scheme and host are what must be reconstructed — the address the platform
saw, not the one the application server was reached on.

**The conversation is the number.** One sender number is one conversation; there is no
richer identity in SMS and none is invented. A text from the channel's own number is
this installation hearing itself and is dropped, which is the echo guard the webhook
channels all need. Dedup is by the platform's own message id: retries are normal.

**Nothing here is rich.** No buttons, no lists, no attachments — 1600 characters of
plain text, split on word boundaries when the answer is longer. That is the whole
interface, which is why SMS needs the full step machine the phone needs (§B13).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import sys
from types import ModuleType
from typing import Any
from urllib.parse import parse_qsl

import httpx
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import routing
from api.channels import generic
from api.channels.generic import ChannelRefused, secrets_of, shown_of
from api.channels.setup import Field, Setup
from api.models import Channel

logger = logging.getLogger("api.sms")


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


KIND = "sms"
INBOUND = "door"

SETUP = Setup(
    kind=KIND,
    title="SMS",
    note="Texts to a number in your own messaging account are answered like any "
    "other conversation.",
    guide_url="https://www.twilio.com/docs/messaging/guides/webhook-request",
    fields=(
        Field(
            "account_sid",
            "Account SID",
            secret=False,
            help="The account identifier shown on your messaging account's console.",
            placeholder="AC…",
        ),
        Field(
            "auth_token",
            "Auth token",
            secret=True,
            help="The auth token of the same account. It also verifies every "
            "delivery that arrives at the address below.",
        ),
        Field(
            "from_number",
            "Sender number",
            secret=False,
            help="The number in that account which receives and sends these texts, "
            "in international format.",
            placeholder="+431234567",
        ),
    ),
    verified_live=False,
)

# Where the messaging REST API lives. Tests replace `make_client` rather than this,
# which is the only seam either of them needs.
API_BASE = "https://api.twilio.com"
API_VERSION = "2010-04-01"

# One text may carry 1600 characters; a longer answer goes out as several.
MESSAGE_MAX = 1600

# The header the platform proves itself with, and the empty document it waits for.
SIGNATURE_HEADER = "X-Twilio-Signature"
ACKNOWLEDGEMENT = '<?xml version="1.0" encoding="UTF-8"?><Response/>'


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(15.0))


def _account(credentials: dict[str, str]) -> tuple[str, str]:
    """(account id, auth token) — what every call to the platform is made as."""
    return str(credentials.get("account_sid") or ""), str(credentials.get("auth_token") or "")


def _refusal(response: httpx.Response) -> ChannelRefused:
    """What a rejected call may be recorded as — the status, never the body.

    The error body of a rejected call echoes the request back, and a request to this
    API carries the account id in its path. The code is what an operator can act on.
    """
    return ChannelRefused(f"the platform answered {response.status_code}")


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The account this token belongs to, by its own name — the test button (§A6.8)."""
    account_sid, auth_token = _account(credentials)
    response = await client.get(
        f"/{API_VERSION}/Accounts/{account_sid}.json", auth=(account_sid, auth_token)
    )
    if response.status_code >= 400:
        raise _refusal(response)
    body = response.json()
    named = body.get("friendly_name") if isinstance(body, dict) else None
    return str(named or account_sid)


def split_text(text: str, limit: int) -> list[str]:
    """One answer as the platform's text-sized pieces, cut between words.

    The cut itself is `generic.split_on_words`, which every channel of this wave
    shares; what this module owns is the limit above it.
    """
    return generic.split_on_words(text, limit)


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One text out to one number.

    One, not an answer of any length: `generic.deliver` is what cuts a long answer
    into texts through `split_text` above, so that the human-takeover route and the
    agent's own reply divide it exactly alike.
    """
    account_sid, auth_token = _account(credentials)
    from_number = str(credentials.get("from_number") or "")
    response = await client.post(
        f"/{API_VERSION}/Accounts/{account_sid}/Messages.json",
        auth=(account_sid, auth_token),
        data={"From": from_number, "To": target, "Body": text},
    )
    if response.status_code >= 400:
        raise _refusal(response)


# --- The signature --------------------------------------------------------------


def signature_for(auth_token: str, url: str, params: dict[str, str]) -> str:
    """The platform's own scheme: the URL it called, then the parameters in key order.

    Key and value concatenated with no separator, HMAC-SHA1 under the auth token,
    base64. SHA-1 is the platform's choice and not this product's; there is nothing to
    pick here, only a contract to keep.
    """
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(
        auth_token.encode(),
        payload.encode(),
        # The platform signs with SHA-1; this only checks what it produced.
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    auth_token: str, url: str, params: dict[str, str], header: str | None
) -> bool:
    """Whether this header is the signature this delivery should carry.

    The ASCII test is not a formality: `hmac.compare_digest` on two `str` raises
    `TypeError` the moment either of them holds a character above U+007F, so a header
    of Arabic text would be a 500 from the door rather than the one refusal every other
    reason gets. A real signature is base64 and cannot contain one.
    """
    if not header or not header.isascii():
        return False
    return hmac.compare_digest(header, signature_for(auth_token, url, params))


def public_url(request: Request) -> str:
    """The address the platform called, which is the address it signed.

    One line, because the reconstruction is `generic.public_url_for` and every door
    channel of this wave owes the same answer as the settings card that printed the
    address. The settings are read from the application rather than the process so a
    test can stand a different installation up beside this one.
    """
    from api.config import get_settings

    settings = getattr(request.app.state, "settings", None) or get_settings()
    return generic.public_url_for(request, settings)


# --- The conversation half ------------------------------------------------------


def message_text(event: dict[str, Any], identity: str) -> str | None:
    """The customer's words out of one delivery, or None when it is not one.

    None for a text from the number this channel speaks from — that is this
    installation hearing itself, and answering it is how a number texts itself
    forever — for a delivery with no sender, and for one with no words: a picture with
    no caption carries nothing to answer.
    """
    sender = str(event.get("From") or "").strip()
    if not sender or (identity and sender == identity):
        return None
    return str(event.get("Body") or "").strip() or None


async def ingest(db: DbSession, channel: Channel, event: dict[str, Any]) -> int | None:
    """Store one delivery. Returns the stored line's id when a reply is due.

    Storage only, so the door can acknowledge inside its budget — the answer is
    `respond`'s job, on its own task and its own session.
    """
    from api.channels import health

    # A delivery that verified is the platform proving the address works; a door
    # channel has no poll to prove it with.
    await health.report_ok(db, channel)

    # The shown half only: the number this channel speaks from is plain in
    # `settings_json`, and the §B9 rule is that nothing decrypts on the inbound path
    # except the signature check itself.
    text = message_text(event, str(shown_of(channel).get("from_number") or ""))
    if text is None:
        return None
    sender = str(event["From"]).strip()

    # Milestone 4: the rules engine, before anything is stored.
    decision = await routing.decide(db, workspace_id=channel.workspace_id, identities=[sender])
    if decision.action == "block":
        logger.info(
            "text message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await generic.conversation_for(db, channel, sender)
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)

    if generic.seen_before(conversation, KIND, str(event.get("MessageSid") or "")):
        logger.info(
            "text delivery repeated, dropped",
            extra={"conversation_id": conversation.id},
        )
        return None

    line = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "text reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """The shared answer path, told which transport it is answering through.

    Nothing here is SMS-specific: its own session, takeover read before generating and
    again before sending, delivery before storage. `generic.respond` is where that
    lives, so the next fourteen channels inherit it instead of copying it.
    """
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The door -------------------------------------------------------------------


async def _parameters(request: Request) -> dict[str, str]:
    """What was signed alongside the address: the POST form, or nothing at all.

    A GET is signed over the URL alone — its query string is already part of that —
    so there is nothing further to concatenate.
    """
    if request.method != "POST":
        return {}
    raw = await request.body()
    return dict(parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True))


async def receive(db: DbSession, channel: Channel, request: Request) -> Response:
    """One delivery: verify, store, acknowledge, answer afterwards.

    Every failure raises, and the public route turns any raise into the one refusal
    every other reason gets. There is nothing to say to a stranger here.
    """
    auth_token = str(secrets_of(channel).get("auth_token") or "")
    if not auth_token:
        raise ChannelRefused("this channel has no auth token to verify with")

    params = await _parameters(request)
    if not verify_signature(
        auth_token, public_url(request), params, request.headers.get(SIGNATURE_HEADER)
    ):
        logger.info(
            "text delivery refused",
            extra={"reason": "bad signature", "channel_id": channel.id},
        )
        raise ChannelRefused("the signature did not check out")

    channel_id = channel.id
    needs_reply = await ingest(db, channel, params) if params else None
    if needs_reply is not None:
        schedule_reply(request.app.state.sessionmaker, channel_id, needs_reply)
    # An empty document, immediately: the answer travels by the API, not by this reply.
    return Response(content=ACKNOWLEDGEMENT, media_type="text/xml")
