"""Microsoft Teams — a bot registration the customer owns, reached through the connector.

The second channel on the declarative contract (D-044): a `Setup` beside the transport,
the generic card, the generic door, and no routes of its own. The credentials belong to
the customer's own bot registration; Tel-Agent holds no shared application here any more
than it does anywhere else.

**Teams is a channel under the same test the other workplace messengers pass** (§B13):
an outside customer reaching a business is a route in, an internal workspace is not. The
card says so in one sentence, because it is the difference between a channel and an
integration and the operator is the one who decides which they are configuring.

**Inbound is a door.** The connector posts one *activity* as JSON and expects 200 at
once; it retries a webhook that answers late, and an answer generated inside the request
would reach the customer twice. The door verifies, stores, acknowledges, and answers
from a task afterwards.

**The proof is a token, not a body signature.** The connector sends
`Authorization: Bearer <JWT>`, signed by a key it publishes: the metadata document names
a key set, the key set holds the RSA keys, and the three claims that make a token ours
are the issuer, the audience — our own application id — and the expiry.
`api/channels/jwt.py` checks all three and refuses RS256's absence, so nothing about
tokens is written twice in this product.

**The answer does not go back where the request came from.** An activity carries a
`serviceUrl` for the region the conversation lives in, and it is the only place that
address is ever stated. It is kept with the thread at ingest, and `reply_target` builds
the conversation's activities address out of it, so the agent's reply and a person's
takeover reply travel by exactly the same route.

**And that address is checked against the token before it is kept.** The body is not
signed; only the token is. An unchecked `serviceUrl` is therefore a stranger's choice
of where this bot posts its own access token, so the token's `serviceurl` claim and the
activity's field must be the same `https` address or the delivery is refused.

**Outbound needs a token of our own**, from the login endpoint, under the application's
own credentials. It lasts an hour, so it is cached until shortly before it expires: one
fetched per message would be one extra round trip on every answer.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from types import ModuleType
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession as DbSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from api import routing
from api.channels import generic, jwt
from api.channels.generic import ChannelRefused, secrets_of, shown_of
from api.channels.setup import Field, Setup
from api.models import Channel, Conversation

logger = logging.getLogger("api.teams")


def _self() -> ModuleType:
    """This module, for the shared helpers that are handed the transport they serve."""
    return sys.modules[__name__]


KIND = "teams"
INBOUND = "door"

SETUP = Setup(
    kind=KIND,
    title="Microsoft Teams",
    note="Answers customers who reach your Teams bot from outside your organisation. "
    "Internal chat is not a channel.",
    guide_url=(
        "https://learn.microsoft.com/azure/bot-service/rest-api/"
        "bot-framework-rest-connector-authentication"
    ),
    fields=(
        Field(
            "app_id",
            "Application (client) ID",
            secret=False,
            help="From your own bot registration. It is also what every delivery "
            "that arrives at the address below is checked against.",
            placeholder="00000000-0000-0000-0000-000000000000",
        ),
        Field(
            "app_password",
            "Client secret",
            secret=True,
            help="A client secret for the same registration.",
        ),
        Field(
            "tenant_id",
            "Directory (tenant) ID",
            secret=False,
            required=False,
            help="Only for a registration that is limited to a single organisation. "
            "Leave this empty for a multi-tenant one.",
            placeholder="00000000-0000-0000-0000-000000000000",
        ),
    ),
    verified_live=False,
)

# Where the connector publishes what it signs with, and who it says it is.
OPENID_CONFIGURATION = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"

# Where a token of our own comes from. A multi-tenant registration is issued one by the
# framework's own directory; a single-tenant registration by the organisation's.
LOGIN_BASE = "https://login.microsoftonline.com"
DEFAULT_DIRECTORY = "botframework.com"
TOKEN_SCOPE = "https://api.botframework.com/.default"  # noqa: S105 - a scope, not a secret

# How long before a token actually expires it is treated as expired. A minute covers a
# clock that disagrees and a request already in flight.
TOKEN_EARLY_SECONDS = 60

# One activity carries 4000 characters; a longer answer goes out as several.
MESSAGE_MAX = 4000

# The header the connector proves itself with, and the scheme it uses.
AUTHORIZATION_HEADER = "Authorization"
BEARER = "bearer"

# What the connector puts in front of an application id to make a bot's own id.
BOT_PREFIX = "28:"

# The one conversation type that is a private chat between one person and the bot.
# Anything else — a team channel, a group chat, a meeting — carries other people.
PERSONAL = "personal"

# The claim the connector states the conversation's regional address in. The activity
# body is not signed, so this is the only statement of that address anybody has proved.
SERVICE_URL_CLAIM = "serviceurl"

# `<at>Name</at>` around the bot's own name, which the connector puts in the text and
# describes again in `entities`. It is markup addressed to the platform, not words
# addressed to us, so it never reaches the model.
_MENTION_TAG = re.compile(r"<at\b[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)

# The metadata document and the outbound token, both cached: the first because a fetch
# per delivery would put two round trips on the door, the second because a token lasts
# an hour and one per message is one call per message.
_METADATA: dict[str, tuple[float, str]] = {}
_TOKENS: dict[str, tuple[float, str]] = {}


def reset_token_cache(channel_id: int | None = None) -> None:
    """Drop the token held for one channel — or, with no channel named, everything.

    Called when an operator writes this channel's credentials: a token bought with the
    secret that was just replaced would otherwise keep working for the rest of its
    hour, which is a rotation that did not take effect. The metadata document is the
    platform's and not the channel's, so it only goes when everything does.
    """
    if channel_id is None:
        _METADATA.clear()
        _TOKENS.clear()
        return
    _TOKENS.pop(str(channel_id), None)


def credentials_changed(channel_id: int) -> None:
    """The generic write route's hook: this channel's credentials were just rewritten."""
    reset_token_cache(channel_id)


def make_client() -> httpx.AsyncClient:
    """No base address: every call this channel makes names its own host.

    The metadata document, the key set, the login endpoint and the conversation's own
    region are four different hosts, and the last of them is chosen by the platform per
    conversation rather than by us.
    """
    return httpx.AsyncClient(timeout=httpx.Timeout(15.0))


def _refusal(response: httpx.Response) -> ChannelRefused:
    """What a rejected call may be recorded as — the status, never the body.

    A rejected token request echoes back what was sent, and what was sent is the
    application's client secret. The status code is what an operator can act on.
    """
    return ChannelRefused(f"the platform answered {response.status_code}")


def _directory(credentials: dict[str, str]) -> str:
    return str(credentials.get("tenant_id") or "").strip() or DEFAULT_DIRECTORY


# --- Tokens ---------------------------------------------------------------------


async def _jwks_url(client: httpx.AsyncClient) -> str:
    """The key set the connector currently signs with, as its metadata document names it.

    Read from the document rather than hard-coded, because the address of the key set is
    the connector's to move and the document is the contract that says where it is.
    """
    cached = _METADATA.get(OPENID_CONFIGURATION)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]

    try:
        response = await client.get(OPENID_CONFIGURATION)
    except httpx.HTTPError as error:
        raise ChannelRefused(f"the metadata document could not be fetched: {error}") from error
    if response.status_code >= 400:
        raise _refusal(response)

    try:
        body = response.json()
    except ValueError as error:
        raise ChannelRefused("the metadata document is not JSON") from error
    address = str(body.get("jwks_uri") or "") if isinstance(body, dict) else ""
    if not address:
        raise ChannelRefused("the metadata document names no key set")

    _METADATA[OPENID_CONFIGURATION] = (time.monotonic() + jwt.JWKS_SECONDS, address)
    return address


async def _access_token(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """A token for calling the connector, cached per channel until it nearly expires."""
    app_id = str(credentials.get("app_id") or "")
    password = str(credentials.get("app_password") or "")
    directory = _directory(credentials)
    if not app_id or not password:
        raise ChannelRefused("this channel has no application credentials")

    # The channel row, not the registration: two workspaces may configure the same
    # application with different secrets, and a token bought by one must never answer
    # for the other. `generic.credentials_of` carries the row's id for exactly this.
    # Without one — a caller assembling a credential dict by hand — nothing is cached,
    # which costs a round trip and confuses nothing.
    holder = str(credentials.get(generic.CHANNEL_ID) or "")
    held = _TOKENS.get(holder) if holder else None
    if held is not None and held[0] > time.monotonic():
        return held[1]

    response = await client.post(
        f"{LOGIN_BASE}/{directory}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": app_id,
            "client_secret": password,
            "scope": TOKEN_SCOPE,
        },
    )
    if response.status_code >= 400:
        raise _refusal(response)
    try:
        body = response.json()
    except ValueError as error:
        raise ChannelRefused("the login endpoint did not answer with JSON") from error

    token = str(body.get("access_token") or "") if isinstance(body, dict) else ""
    if not token:
        raise ChannelRefused("the login endpoint issued no token")
    lifetime = body.get("expires_in")
    seconds = float(lifetime) if isinstance(lifetime, int | float) else 0.0
    if holder:
        _TOKENS[holder] = (
            time.monotonic() + max(seconds - TOKEN_EARLY_SECONDS, 0.0),
            token,
        )
    return token


async def probe(client: httpx.AsyncClient, credentials: dict[str, str]) -> str:
    """The application these credentials belong to — the test button (§A6.8).

    The framework publishes no "who am I" call: a bot is known by the registration it
    speaks as, and the proof that the registration and its secret agree is that a token
    is issued at all. So the identity reported is the application id, and obtaining the
    token is the test.
    """
    await _access_token(client, credentials)
    return str(credentials.get("app_id") or "")


# --- Sending --------------------------------------------------------------------


def split_text(text: str, limit: int) -> list[str]:
    """One answer as the platform's message-sized pieces, cut between words.

    The cut itself is `generic.split_on_words`, which every channel of this wave
    shares; what this module owns is the limit above it.
    """
    return generic.split_on_words(text, limit)


async def send_text(
    client: httpx.AsyncClient, credentials: dict[str, str], target: str, text: str
) -> None:
    """One activity out to one conversation.

    `target` is the conversation's own activities address, which `reply_target` builds
    from what was remembered at ingest — the connector's regional service address is not
    knowable from a credential, only from a message that arrived.
    """
    token = await _access_token(client, credentials)
    response = await client.post(
        target,
        headers={AUTHORIZATION_HEADER: f"Bearer {token}"},
        json={"type": "message", "text": text},
    )
    if response.status_code >= 400:
        raise _refusal(response)


# --- The conversation half ------------------------------------------------------


def _without_mention(text: str) -> str:
    """The customer's words with the markup around our name taken out."""
    return " ".join(_MENTION_TAG.sub(" ", text).split())


def _is_us(candidate: str, identity: str, bot_id: str) -> bool:
    """Whether one platform id is this installation's own bot.

    Exact, both ways round. A bot's id is the application id behind the connector's
    `28:` prefix, so both shapes are accepted — but only whole: `identity in candidate`
    would call `28:<our id>-something-else` us, and an id is not a substring match.
    """
    if not candidate:
        return False
    if bot_id and candidate == bot_id:
        return True
    if not identity:
        return False
    bare = candidate[len(BOT_PREFIX) :] if candidate.startswith(BOT_PREFIX) else candidate
    return bare == identity


def _addressed(event: dict[str, Any], identity: str, bot_id: str) -> bool:
    """Whether this message names the bot, per the mentions the platform describes.

    The `entities` list is where a mention is stated unambiguously; the `<at>` markup in
    the text is the same mention rendered, and is not read as evidence on its own —
    somebody quoting our name is not somebody addressing us.
    """
    for entity in event.get("entities") or []:
        if not isinstance(entity, dict) or str(entity.get("type") or "") != "mention":
            continue
        mentioned = entity.get("mentioned")
        named = str(mentioned.get("id") or "") if isinstance(mentioned, dict) else ""
        if _is_us(named, identity, bot_id):
            return True
    return False


def _is_group(conversation: dict[str, Any]) -> bool:
    """Whether this conversation carries other people's talk as well as ours.

    `isGroup` is the plain statement of it, and a conversation that states a type of
    anything but `personal` — a team channel, a group chat, a meeting — is one too.
    Reading only `isGroup` answers a whole team channel as though it were a private
    chat, which is the mention rule not applying where it matters most.
    """
    if bool(conversation.get("isGroup")):
        return True
    stated = str(conversation.get("conversationType") or "").strip().lower()
    return bool(stated) and stated != PERSONAL


def message_text(event: dict[str, Any], identity: str) -> str | None:
    """The customer's words out of one activity, or None when there are none to answer.

    None for every activity that is not a message — somebody adding the bot to a team,
    a typing indicator, a read receipt — for one the bot itself or another bot sent, for
    one in a group conversation that does not address us, and for one that is nothing
    but the mention.
    """
    if str(event.get("type") or "") != "message":
        return None

    sender = event.get("from") or {}
    sender_id = str(sender.get("id") or "")
    if not sender_id or str(sender.get("role") or "").lower() == "bot":
        return None

    # The recipient of an inbound activity is the bot, so this is where it learns its
    # own id — the credential half of it is never read on the inbound path (§B9).
    bot_id = str((event.get("recipient") or {}).get("id") or "")
    if _is_us(sender_id, identity, bot_id):
        return None

    conversation = event.get("conversation") or {}
    if _is_group(conversation) and not _addressed(event, identity, bot_id):
        return None

    return _without_mention(str(event.get("text") or "")) or None


def _remember(conversation: Conversation, service_url: str, conversation_id: str) -> None:
    """Where this thread is answered, kept beside it.

    The regional service address arrives with each message and is stated nowhere else,
    so an installation that only ever read credentials would have no address to reply
    to. Written before dedup, which merges into the same object.
    """
    state = dict((conversation.state_json or {}).get(KIND) or {})
    if state.get("service_url") == service_url and state.get("conversation_id") == (
        conversation_id
    ):
        return
    state["service_url"] = service_url
    state["conversation_id"] = conversation_id
    # Reassigned rather than mutated: a JSON column changed in place is not dirty.
    conversation.state_json = {**(conversation.state_json or {}), KIND: state}


def reply_target(conversation: Conversation) -> str | None:
    """The conversation's activities address — where an answer on this thread goes.

    `external_id` is the conversation id alone, which is not an address: the region the
    conversation lives in is chosen by the platform. A thread that never carried one is
    a thread nothing can be sent to, and saying so is better than posting somewhere.
    """
    state = (conversation.state_json or {}).get(KIND) or {}
    service_url = str(state.get("service_url") or "").rstrip("/")
    conversation_id = str(state.get("conversation_id") or conversation.external_id or "")
    if not service_url or not conversation_id:
        return None
    return f"{service_url}/v3/conversations/{quote(conversation_id, safe='')}/activities"


async def ingest(db: DbSession, channel: Channel, event: dict[str, Any]) -> int | None:
    """Store one activity. Returns the stored line's id when a reply is due.

    Storage only, so the door can acknowledge inside its budget — the answer is
    `respond`'s job, on its own task and its own session.
    """
    from api.channels import health

    # A delivery that verified is the platform proving the address works; a door channel
    # has no poll to prove it with.
    await health.report_ok(db, channel)

    # The shown half only: the application id is plain in `settings_json`, and the §B9
    # rule is that nothing decrypts on the inbound path except the proof of identity.
    text = message_text(event, str(shown_of(channel).get("app_id") or ""))
    if text is None:
        return None

    conversation_id = str((event.get("conversation") or {}).get("id") or "")
    # Normalised here rather than as it arrives, so what is kept beside the thread is
    # one spelling of the address and never `http`. `receive` has already checked it
    # against the token; this is what makes `reply_target` unable to build anything else.
    service_url = _same_address(str(event.get("serviceUrl") or ""))
    if not conversation_id or not service_url:
        return None
    sender_id = str((event.get("from") or {}).get("id") or "")

    # Milestone 4: the rules engine, before anything is stored.
    decision = await routing.decide(
        db, workspace_id=channel.workspace_id, identities=[sender_id]
    )
    if decision.action == "block":
        logger.info(
            "teams message dropped by rule",
            extra={"channel_id": channel.id, "pattern": decision.pattern},
        )
        return None

    conversation, started = await generic.conversation_for(db, channel, conversation_id)
    if decision.action == "pass":
        await routing.apply_pass(db, conversation, decision)
    _remember(conversation, service_url, conversation_id)

    if generic.seen_before(conversation, KIND, str(event.get("id") or "")):
        logger.info(
            "teams activity repeated, dropped",
            extra={"conversation_id": conversation.id},
        )
        return None

    line = await generic.store_line(db, conversation, "caller", text)
    await generic.announce(db, channel, conversation, line, started)

    if conversation.handling == "human":
        logger.info(
            "teams reply withheld, a person has the thread",
            extra={"conversation_id": conversation.id},
        )
        return None
    return line.id


async def respond(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    """The shared answer path, told which transport it is answering through."""
    await generic.respond(sessionmaker, _self(), channel_id, message_id)


def schedule_reply(sessionmaker: async_sessionmaker, channel_id: int, message_id: int) -> None:
    generic.schedule_reply(sessionmaker, _self(), channel_id, message_id)


# --- The door -------------------------------------------------------------------


def _bearer_token(request: Request) -> str:
    """The token out of the authorisation header, or a refusal.

    A missing header and an unreadable one are the same answer: this door has one
    refusal for every reason and nothing to gain from telling them apart.
    """
    header = request.headers.get(AUTHORIZATION_HEADER) or ""
    scheme, _, token = header.partition(" ")
    if scheme.strip().lower() != BEARER or not token.strip():
        raise ChannelRefused("no bearer token was presented")
    return token.strip()


def _same_address(stated: str) -> str | None:
    """One service address in a form two spellings of it compare equal in.

    Scheme and host are case-insensitive and a trailing slash means nothing, so all
    three are flattened before the comparison. Anything that is not `https` is not an
    address this channel will hand a bearer token to, and comes back as `None`.
    """
    parsed = urlsplit(stated.strip())
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None
    return f"https://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def _check_service_url(activity: dict[str, Any], claims: dict[str, Any]) -> None:
    """The address the answer will be posted to has to be one the connector signed.

    **The activity body is not signed — the token is.** So a stranger holding any valid
    connector token could otherwise state a `serviceUrl` of their own, and `send_text`
    would post this bot's access token to it. The token states the address it was issued
    for in its own claims, and the two have to be the same address; a token that states
    none proves nothing about where the answer goes, and is refused.
    """
    signed = _same_address(str(claims.get(SERVICE_URL_CLAIM) or ""))
    if signed is None:
        raise ChannelRefused("the token names no service address")
    if _same_address(str(activity.get("serviceUrl") or "")) != signed:
        raise ChannelRefused("the activity names a service address the token did not")


async def receive(db: DbSession, channel: Channel, request: Request) -> Response:
    """One activity: verify, store, acknowledge, answer afterwards.

    Every failure raises, and the public route turns any raise into the one refusal
    every other reason gets. There is nothing to say to a stranger here.
    """
    app_id = str(shown_of(channel).get("app_id") or "")
    if not app_id:
        raise ChannelRefused("this channel has no application to verify against")
    # Read so that a channel whose secret was cleared cannot answer on a stale one; the
    # value itself is not part of the inbound proof.
    if not secrets_of(channel).get("app_password"):
        raise ChannelRefused("this channel has no client secret")

    token = _bearer_token(request)
    async with make_client() as client:
        claims = await jwt.verify_rs256(
            token,
            jwks_url=await _jwks_url(client),
            issuer=ISSUER,
            audience=app_id,
            client=client,
        )

    raw = await request.body()
    try:
        activity = json.loads(raw.decode("utf-8", "replace")) if raw else None
    except ValueError as error:
        raise ChannelRefused("the body is not JSON") from error
    if not isinstance(activity, dict):
        raise ChannelRefused("the body is not an activity")

    _check_service_url(activity, claims)

    channel_id = channel.id
    needs_reply = await ingest(db, channel, activity)
    if needs_reply is not None:
        schedule_reply(request.app.state.sessionmaker, channel_id, needs_reply)
    # 200 with nothing in it, immediately: the answer travels as its own activity, and
    # every type this channel does not answer is acknowledged exactly the same way.
    return Response(status_code=200)
