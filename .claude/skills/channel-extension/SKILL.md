---
name: channel-extension
description: Use when adding a new messaging channel to Tel-Agent or changing an existing one — a transport under api/channels/, its official extension manifest, its settings card, tests, health and docs. Covers the file checklist, the two inbound shapes (dial-out and the public door), the credential and signature rules that are not negotiable, the declarative setup descriptor the generic card draws from, and the test shape every channel ships with.
---

# Building a channel extension

You are adding a messaging channel to Tel-Agent. A channel is a route a **customer**
uses to reach a business — someone is typing on the other end. If nobody outside the
business is on the other end, it is an integration, not a channel, and it is reached
through the HTTP tool instead (Rule 5 in `CLAUDE.md`). Do not build it here.

**Read first, in this order:** `CLAUDE.md` (the contract), `docs/SPEC.md` §B9 (secrets)
and §B13 (channels), then `api/channels/discord.py` end to end. Discord is the model
every channel copies: a dial-out transport with a supervisor loop, an answering policy,
storage with dedup, and delivery-before-storage. For a channel that receives webhooks,
also read `api/channels/meta_chat.py` and `api/routes/meta_chat_channels.py` — that is
the public door, and its guards are the whole of this product's exposed surface.

Write everything from the platform's **official documentation** and from the shape of
the existing modules. Write it fresh. Never paste code from another product into this
repository, never name another product anywhere in it, and never add a runtime
dependency — `httpx`, `websockets`, `cryptography` and the standard library cover every
platform we ship.

## What a channel consists of

One kind, one transport module, one manifest, one descriptor, one test file. In order:

| # | Path | What goes there |
|---|---|---|
| 1 | `api/channels/<kind>.py` | The transport. Module surface below. |
| 2 | `api/extensions/builtin/<kind>.py` | `MANIFEST` + `register(context)`. Copy the Discord one; change slug, name, description. Category `channels`, origin `official`, scopes `conversations.write`, `messages.read`, `messages.write`, hooks `message.received`, ui_slots `conversation.detail`. |
| 3 | `api/extensions/builtin/__init__.py` | Append the module to `BUILTIN`. |
| 4 | `api/models/conversation.py` | The kind is already in `CHANNEL_KINDS` (one migration added every kind of this wave). If yours is not, add it **and** an Alembic revision that widens the `channel_kind` CHECK constraint — `tests/test_migrations.py` runs it on SQLite and PostgreSQL. |
| 5 | `api/channels/generic.py` | Add the module's import path to `_DECLARED`. The registry imports it on first read; that is what serves its card and its routes. |
| 6 | `api/main.py` | Dial-out channels: append `transport.loop(sessionmaker)` to `background`. Door channels: nothing — the generic public route dispatches by kind. |
| 7 | `tests/test_<kind>_channel.py` | The test file. Shape below. |
| 8 | `web/components/brands/marks.tsx` + `source/<kind>.svg` | The mark, when the platform has a logo. Record the source and licence in `web/components/brands/README.md`. A channel with no owner (SMS, IRC) gets a drawn glyph in `web/components/shell/channel-mark.tsx`. |
| 9 | `web/app/[locale]/settings/settings.tsx` | Add the kind to `GENERIC_CHANNELS`. The card draws itself from the descriptor. |
| 10 | `locales/en/web.json` | The display name under `"channels"`. Other languages follow through the translation pipeline; do not touch thirty-four files. |
| 11 | `docs/SPEC.md` §B13, `README.md` icon row, `docs/brand/channels/` | One row, one icon, one table line. |

## The transport module surface

Every module exposes the same names, so the generic route, the health rollup and the
tests can treat them alike. Signatures are the ones `api/channels/discord.py` has;
keep them.

```python
KIND = "<kind>"
SETUP = Setup(...)                                   # the descriptor, see below
INBOUND = "dial_out"  # or "door"

def make_client() -> httpx.AsyncClient: ...          # tests monkeypatch this
async def probe(client, credentials: dict[str, str]) -> str: ...
    # "Test connection". Returns the identity the platform reports (bot name, number,
    # account) — the card shows it. Raises ChannelRefused on a bad credential.
async def send_text(client, credentials, target: str, text: str) -> None: ...
def message_text(event, identity) -> str | None: ... # the answering policy: None = ignore
async def ingest(db, channel, event) -> int | None: ...
    # store one inbound line; None when it was a duplicate or not for us
async def respond(sessionmaker, channel_id: int, message_id: int) -> None: ...
def schedule_reply(sessionmaker, channel_id: int, message_id: int) -> None: ...

# optional
def credentials_changed(channel_id: int) -> None: ...
    # What the module is told after the operator writes this channel's fields, so it can
    # drop anything it cached on the strength of the old ones — an access token bought
    # with a secret that has just been replaced would otherwise keep working until it
    # expired, which is a rotation that did not take effect. A module that caches nothing
    # declares nothing; `api/routes/generic_channel.py` calls it only when it is there.

def reply_target(conversation) -> str | None: ...
    # The address `send_text` needs for this thread, when it is not the conversation's
    # own `external_id` — a room id, a thread key, a mailbox. Declare nothing and
    # `generic.reply_target_of` falls back to `external_id`. It is what the human
    # takeover route delivers to as well, so a channel that answers somewhere other
    # than where the customer wrote from owes this one function and nothing else.

# dial-out only
async def loop(sessionmaker) -> None: ...            # the supervisor, reconciles every 15 s

# door only
async def receive(db, channel, request: Request) -> Response: ...
    # verify, dedup, store, schedule, acknowledge. One refusal for every failure.
```

`credentials` is the decrypted JSON of `Channel.credentials_encrypted` — a dict keyed by
the descriptor's field names. Never read it on the inbound hot path for anything other
than a signature check; per-channel state that every message needs lives in
`settings_json`, which is plain.

### Shared helpers — use these, do not copy Discord

`api/channels/generic.py` holds the half of a transport that is the same on every
channel. A declarative channel writes the platform-specific half only: its descriptor,
its signature check, `probe`, `send_text`, `message_text`, `ingest`, and its `receive`
or `loop`. `split_text` is a line delegating to `split_on_words` below. Everything below
is already written.

| Helper | What it does |
|---|---|
| `channels()` | The registry, populated on first read. `module_for(kind)` and `dial_out_modules()` go through it, so no import order can change what an installation has. Never read `CHANNELS` directly. |
| `public_url_for(request, settings)` | The address the platform called, for a signature computed over it. `PUBLIC_BASE_URL` when the installation has one, else the forwarded headers — the one place in this product where those are read, and the reasoning is in the function. The settings card builds its `webhook_url` on the same base, so the two cannot disagree. |
| `conversation_for(db, channel, external_id, title=None)` | This customer's open thread, and whether it was just started. |
| `store_line(db, conversation, role, text, language=None)` | One line into the transcript. |
| `seen_before(conversation, kind, message_id)` | Dedup. A ring of the last 32 platform ids in `state_json[kind]`, because a platform does not retry in order. |
| `announce(db, channel, conversation, message, started)` | The `conversation.started` and `message.received` hooks. |
| `preview(text)` | One line of a customer's words, for a notification. |
| `reply_target_of(module, conversation)` | Where a reply goes: the module's `reply_target`, else `external_id`. |
| `split_on_words(text, limit)` | The cut every channel shares: pieces no longer than `limit`, never inside a word, never silently short, and a single word longer than the limit cut rather than dropped. A module's `split_text` is one line handing this its own `MESSAGE_MAX`. |
| `deliver(module, client, credentials, target, text)` | One answer out, cut into the platform's messages through the module's `split_text`. |
| `schedule_reply(sessionmaker, module, channel_id, message_id)` | The answer as its own task, held so it cannot be collected mid-reply. |
| `respond(sessionmaker, module, channel_id, message_id)` | The whole answer path: its own session, takeover read before generating **and again before sending**, delivery before storage, health timing. |

A module still exposes `respond` and `schedule_reply` itself — the registry requires
them — but each is one line handing the shared one this module:

```python
def _self() -> ModuleType:
    return sys.modules[__name__]

async def respond(sessionmaker, channel_id: int, message_id: int) -> None:
    await generic.respond(sessionmaker, _self(), channel_id, message_id)
```

The human takeover route needs no branch per channel: `api/routes/conversations.py`
delivers through the registry for any kind on it.

## The setup descriptor

The card, the write route and the test button are generic. What makes one channel's
card differ from another's is this object, declared once in the transport module:

```python
from api.channels.setup import Field, Setup

SETUP = Setup(
    kind="line",
    title="LINE",
    note="An official account from your own LINE Developers console answers people who message it.",
    guide_url="https://developers.line.biz/en/docs/messaging-api/getting-started/",
    fields=(
        Field("channel_secret", "Channel secret", secret=True,
              help="From the Basic settings tab of your channel."),
        Field("channel_access_token", "Channel access token", secret=True,
              help="Issue a long-lived token on the Messaging API tab."),
    ),
    verified_live=False,   # True once a real message has gone through on a customer's account
)
```

- `secret=True` fields are write-only: the API returns a masked preview (last four
  characters), never the value. `secret=False` fields are returned as stored.
- `required=False` fields may be left empty. The card shows them under "Optional".
- The English `label` and `help` are what the card shows unless a locale key
  `ch_<kind>_<field>` / `ch_<kind>_<field>_help` exists. Do not add locale keys for a
  new channel yourself; the translation pipeline does.
- `verified_live=False` prints "built against the published API, not yet verified with
  a live account" on the card. It is honest and it stays until someone flips it after
  a real message.
- A door channel's card also shows its public URL, built from `Channel.webhook_path`.

The generic routes are `GET /api/channels/{kind}` (descriptor + state),
`PUT /api/channels/{kind}` (fields, `enabled`), `POST /api/channels/{kind}/test`, and for
doors `GET|POST /public/{kind}/{webhook_path}`. They live in
`api/routes/generic_channel.py` and `api/routes/public_channel.py`; you do not write
routes for a new channel.

## Rules that are not negotiable

**Credentials — §B9.** Encrypted at rest in `Channel.credentials_encrypted`, one JSON
object for all of a channel's secrets. Write-only: an empty string clears the field, a
value starting with `•` or `*` is an echoed mask and is ignored, anything else is stored.
Clearing the last required secret switches the channel off. Storing requires the
installation's encryption key (409 `encryption_key_missing` without it). Never log a
credential, never return one, never put one in a URL. Every write is audited.

**The customer's own application.** Tel-Agent holds no shared platform app. Every
credential comes from the customer's own developer account. If a platform only works
through an application the vendor owns, it cannot be a Tel-Agent channel.

**The door — for every channel that receives webhooks.**
1. The address is a long random path (`secrets.token_urlsafe(24)`), unique per channel,
   stored in `Channel.webhook_path`. The route is `/public/{kind}/{webhook_path}`.
2. Verify the platform's signature over the **raw body bytes**, before parsing anything,
   with `hmac.compare_digest`. A JWT is verified against the issuer's published keys
   with `cryptography`, checking `iss`, `aud`, `exp`. No signature scheme, no channel.
3. **One refusal for every reason.** Unknown path, wrong signature, disabled channel,
   malformed body — all of them return the same 403 `not_recognised`. The door leaks
   nothing about what exists behind it.
4. Acknowledge first, answer after. Return the platform's expected 200 immediately;
   generate and deliver the reply in a scheduled task. Platforms retry slow webhooks and
   you will answer twice.
5. Dedup by the platform's own message id before storing. Retries are normal.
6. Signal, Matrix, Mattermost, IRC, iMessage and every other dial-out channel expose
   nothing. Prefer dial-out whenever the platform offers it; the LAN installation is the
   product's reason to exist.

**Answering policy.** A direct message is always answered. A group, room, channel or
space message is answered only when the bot is addressed, and the mention is stripped
before the text reaches the model. A message from the bot itself, or from another bot,
is never answered.

**Delivery before storage.** `respond()` generates the whole reply, delivers it, and
only then writes the agent line. If delivery raises, nothing is written — a transcript
must never show an answer the customer did not receive. Human takeover is checked
before generating and again before sending.

**Reconnection.** Dial-out loops reconnect with exponential backoff from 5 s to 300 s
and report their state through `api/channels/health.py` so the health page can say
"connected" or "reconnecting" truthfully.

**Text limits.** Every platform has one. Split on word boundaries at the platform's
limit; never truncate silently.

**Nothing new in `pyproject.toml`.** If you believe a platform genuinely cannot be done
without an SDK, stop and say so in the report instead of adding it.

## The test file

`tests/test_<kind>_channel.py`, modelled on `tests/test_discord_channel.py`. A fake
platform, not mocked functions:

```python
class FakeLine:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.refuse = False
    def handler(self, request: httpx.Request) -> httpx.Response: ...   # route by path
    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url="https://line.test",
                                 transport=httpx.MockTransport(self.handler))
```

The `stage` fixture builds a workspace, a channel row with credentials, three users at
`admin` / `reception` / `viewer`, monkeypatches `make_client`, and starts the real app.
Copy it.

Every channel's file has at least these, named for what they prove:

- a direct message is answered, and a message from a bot never is
- a group message is answered only when addressed, and the mention is stripped
- a repeated event is dropped by the platform's own id
- a taken-over thread gets no generated reply
- a refused send leaves no agent line in the record
- the secret goes in and only a mask comes out
- removing the secret switches the channel off with it
- a viewer reads and never writes
- the test button reports the platform's identity, and reports refusal
- **door channels:** a wrong signature, an unknown path and a disabled channel all get
  the same refusal; the platform's verification handshake succeeds
- **dial-out channels:** the loop starts a connection for an active channel and stops
  it when the channel is disabled

Run the suite for your file and the shared ones, then the gate:

```bash
pytest tests/test_<kind>_channel.py tests/test_channel_health.py tests/test_extensions.py -q
ruff check . && ruff format --check . && mypy .
npm --prefix web run lint && npx --prefix web tsc --noEmit
```

## Before the commit

- No secret, no real phone number, no product other than the platform itself named.
- English only. Conventional commit: `feat(channels): add <kind>`.
- The card renders with no credentials saved, with a secret saved (mask shown), and
  after a test — check all three in the browser before calling it done.
