# Tel-Agent — Complete Build Specification

> Open-source gateway that connects any phone line to any AI model.
> Self-hosted. Bring your own keys. Full control over who gets through.

**Project:** Tel-Agent · `tel-agent.com` · AGPL-3.0 · maintained by Dpro GmbH (Vienna)
**Hosted edition (later):** Tel-Agent Cloud

This document is the single source of truth for design and implementation.
Section A is for the designer. Section B is for the developer. Read both.

---

# START HERE — Milestone 0

**Goal:** one conversation, answered end to end in a web chat. The reply streams token
by token, it can be interrupted mid-sentence, the message is captured and the
transcript printed.

**Not in this milestone:** no dashboard, no Docker, no database, no routing rules, no
provider abstraction beyond the model. A page and a script.

**Time box: two weeks.** If it isn't working by then, the constraint is time, not
architecture — and knowing that early is worth more than any feature.

**The phone is Milestone 11**, by decision D-017 of 2026-08-22. Everything below about
numbers, trunks and SIP is still correct and still required — it simply happens last.
It is kept here, in order, because it is the milestone that judges every interface
built before it: an agent that cannot stream and cannot cancel will fail on a call no
matter how well it reads in a chat window.

## Milestone 0 in four steps

| # | Step | Done when |
|---|---|---|
| 1 | A page that posts a message | A message typed in a browser reaches the agent process |
| 2 | A model behind one interface | It replies, and swapping the model does not touch the page |
| 3 | **Stream and cancel** | Tokens appear as produced; cancelling stops generation, not just the display |
| 4 | Take a message | It asks for name and reason, prints a structured result |

Step 3 is the one that matters beyond this milestone. It is what the phone will
require, proven while it is cheap.

**Measure from the first exchange:** time to first token, and where that time goes.
The same discipline as the call measurements below, on an easier channel.

---

# Milestone 11 — the phone line

Everything from here to the end of this section is the phone milestone. It was written
first, when the phone was Milestone 0, and none of it has been weakened by moving:
the numbers, the trunk, the codec and the measurements are unchanged.

## Step 1 — A number that reaches the agent (30 minutes)

Milestone 11 needs one inbound number under your own control. Buy it from a SIP
provider — Twilio, Telnyx, or a European equivalent — and point it at a LiveKit Cloud
SIP trunk.

1. Buy a number. A local geographic number in the EU normally needs a regulatory
   bundle (address proof, sometimes an in-country address); budget time for it, or
   start with a number in a country where the requirement is lighter
2. Create an inbound SIP trunk and a dispatch rule in LiveKit Cloud
3. Point the number's voice webhook or SIP URI at that trunk
4. Note down: the **E.164 number**, the **trunk address**, and the **LiveKit API key
   and secret**

**Why not the PBX extension this was originally written around:** it depends on
administrator access to a PBX. Anyone who does not have that access is blocked before
the first line of code, and no amount of Python solves an authorization problem.
Connecting an existing PBX extension remains fully supported and is the better story
for businesses that already run one — it is just not the path that proves the product
first. See §B3.1.

**Verify before writing any code:** call the number from a mobile and confirm in the
provider console that the call arrives and reaches the trunk. If it does, telephony is
fine and every later problem is yours. If it does not, stop here — debugging SIP
through your own code is far harder.

**Record on that first call:** which number appears as the caller. Then set your own
mobile to forward to it after 15 seconds, call that mobile from a third phone, and
check the caller ID again. If forwarding replaces the original caller's number with
the subscriber's, every routing rule in §A6.5 matches the same number on every call.
Better to learn this early than during the phone half of Milestone 4.

## Step 2 — Machine setup

Any machine with outbound internet access. The agent connects **out** to LiveKit; it
accepts no inbound connections, so there is no NAT traversal, no STUN, and no RTP port
range to open. That removes the single biggest source of "call connects but there's no
audio" — and defers it rather than solving it, because the self-hosted media path
brings it back.

```
Python 3.11+
ffmpeg installed
Outbound internet access (LiveKit Cloud, and the STT / LLM / TTS providers)
```

Keys needed:

- Deepgram (STT)
- One cloud LLM (pick the fastest you have access to)
- ElevenLabs (TTS) — you already know this one, so no learning cost

Set `codec = G.711 (PCMU/PCMA)` on the trunk. Clearer to debug than Opus and
fewer compatibility surprises.

## Step 3 — The script

Skeleton in `agent_skeleton.py`. It is deliberately incomplete: the structure, the
interfaces, and the ordering are decided; the SDK calls are marked `TODO` because they
move between library versions and must be taken from current docs, not from memory.

Build it in this order — **verify each before moving on:**

| # | Check | How you know it works |
|---|---|---|
| 1 | The number reaches us | Provider console shows the inbound call reaching the trunk |
| 2 | Answer the call | You call the number, it stops ringing, silence on the line |
| 3 | Speak fixed text | It answers and says a hardcoded greeting |
| 4 | Hear the caller | Your words appear as text in the terminal |
| 5 | Full loop | You speak → LLM replies → you hear the reply |
| 6 | Take a message | It asks for name and reason, prints a structured result |

Step 3 is the moment the project becomes real. Steps 1–2 are plumbing; step 5 is the
product.

## Step 4 — What to measure from the first call

Log these on every call, from day one. They decide whether the product is viable:

- **Time from end of speech to first audio out** — target under 800ms
- **Where the time goes** — STT final / LLM first token / TTS first chunk
- **Interruption handling** — what happens when you talk over it
- **German accuracy** — run at least 20 real calls in Austrian German before trusting
  any STT provider. Include names and addresses; that's where it fails.

If latency is above ~1.5s, do not add features. Fix the streaming first — nothing else
matters until the call feels like a conversation.

## Step 5 — Then, and only then

Once milestone 0 works:

1. Wrap providers in the interfaces from spec §B3
2. Postgres + transcript storage (full-text index in the first migration, `user_id` on
   every table)
3. Design + build the call detail screen (spec §A6.4)
4. Everything else in spec §B11 build order

## Repo setup (do this today, takes 20 minutes)

```
tel-agent/
├── LICENSE          # AGPL-3.0
├── CLA.md           # before the first PR — after that it's practically impossible
├── README.md        # one-liner, what it is, what it is not, quick start
├── IDEAS.md         # everything discussed but not in v1 — parks scope safely
└── agent/
    └── agent_skeleton.py
```

`IDEAS.md` matters more than it looks. Every good idea that arrives mid-build goes
there instead of into the code. It is the mechanism that gets this finished.

## The one rule

Nothing gets built until the call in step 3 works.

Not the UI. Not the rules engine. Not the second provider. Not MCP.

The last phone integration stalled with plenty of plan and no answered call. The only
difference this time is the order.

---

## Decisions locked

These are settled. Do not reopen them without a concrete reason.

| Decision | Choice |
|---|---|
| Name | **Tel-Agent** — one name for everything. Hosted edition is "Tel-Agent Cloud". |
| Domain | `tel-agent.com` |
| License | AGPL-3.0 + CLA from the first contributor |
| Copyright holder | Dpro GmbH |
| Separate from | Agent-Player and Flowxtra — own repo, own identity, no shared code without a written arrangement |
| Backend | Python (agent + FastAPI) |
| Frontend | Next.js |
| Database | PostgreSQL **and SQLite**, both from the first migration (D-029) |
| Voice framework | LiveKit Agents |
| Packaging | Docker Compose (manual dev run also documented) |
| Runs as | Locally installed web app on the LAN — not a desktop app, not SaaS-only |
| First test bed | A number from a SIP provider, pointed at the agent |
| Number acquisition | Users bring their own number in v1. Reselling numbers belongs to Tel-Agent Cloud and never enters the open edition — see §B3.1 |
| SIP at Milestone 11 | LiveKit Cloud SIP. A first-call decision only; the self-hosted media path returns after |
| Theme | Dark and light, dark designed first |
| Languages | Multi-language from day one: en / de / ar, RTL supported |
| Analog phone lines | Out of scope. Users bridge with an ATA; we only ever speak SIP. |
| Workflow automation | Out of scope. Webhooks + generic HTTP tool; n8n does the rest. |
| Messaging channels | In scope at Milestone 3 — web chat, SMS, email, WhatsApp, Telegram, Messenger, Instagram, Discord, Slack. Ten with the phone, and Tel-Agent commits to those ten. **A channel is an extension, so the list is open (D-032);** anything beyond the ten is community-owned and unsupported. Customer connects their own app credentials (§B13). |

---

# PART 0 — What we are building

A self-hosted service that sits between a phone line and an AI agent.

A call arrives over SIP. Tel-Agent checks the caller against routing rules and either
passes it through to a human, blocks it, or hands it to an AI agent. The agent speaks
with the caller in real time, can invoke tools (transfer, take a message, check a
calendar, call any HTTP endpoint), and every call is recorded, transcribed, and
searchable.

## Scope boundary — memorize this

| Tel-Agent owns | Tel-Agent does NOT own |
|---|---|
| Telephony / SIP | General workflow automation |
| Voice pipeline (STT → LLM → TTS) | Integrations with 400 SaaS apps |
| Turn-taking, barge-in | Being a CRM |
| Conversation + memory | Being a PBX replacement |
| Call routing rules | Analog hardware support |
| Transcript archive + search | |
| Tool execution | |
| **Messaging channels** (§B13) | |

Everything outside the left column is reached through **webhooks** and the **generic
HTTP tool**. n8n and Home Assistant do that job better than we would.

**Channel or integration — the distinction that keeps this table finite.**
A **channel** is where the conversation happens: the person is on the other end of it,
speaking or typing. An **integration** is a system the agent acts *on* during that
conversation. Tel-Agent owns channels and reaches integrations through the HTTP tool.
Ten channels are in scope and the list is closed (§B13). Integrations are unbounded by
nature, which is why they are somebody else's product.

## Users

1. **Primary — self-hosters and developers.** Run Docker, edit config, bring their own
   API keys. They found us on GitHub or Hacker News.
2. **Secondary — small businesses** (clinics, workshops, agencies) who buy the hosted
   edition. They must never see a config file.

The UI serves both: working defaults for group 2, an **Advanced** surface for group 1.

## Why this exists

Well-funded closed products already do "AI receptionist for business." What does not
exist is a good **open, self-hosted** one where the user owns the recordings, picks the
models, and decides which callers ever reach the AI at all.

---

# PART A — DESIGN SPECIFICATION

## A1. Design principles

1. **The transcript is the product surface.** Most time in this app is spent reading
   what was said. Optimize for reading comfort above all else.
2. **State must be unmistakable.** Is the system live? Is a call happening right now?
   Did the agent handle it or did a human? Never make the user hunt for this.
3. **Defaults over configuration.** Every field that can have a working default has one.
   Empty inputs make users hesitate.
4. **Advanced is hidden, not absent.** Developers will find it. Everyone else won't
   trip over it.
5. **Nothing depends on string length.** Multi-language from day one (see A4).

## A2. Theme

Dark and light, toggled in the top bar, persisted per user.

**Design dark first.** The primary audience runs self-hosted tools and prefers dark.
Light must be a real second pass, not an inverted dark theme.

## A3. Visual system

**Type**

- UI: clean geometric sans (Inter or similar)
- Monospace for: phone numbers, timestamps, API keys, logs, IDs
- Transcript body: comfortable reading size, generous line height — people read these
  for minutes at a time

**Color**

- One accent, used sparingly: primary actions and live state only
- Semantic set:
  - green — passed through to human
  - red — blocked
  - accent — AI handled
  - amber — needs attention
- **Live call indicator must pulse.** Not a subtle badge.

**Density**

Medium. Denser than a consumer app, lighter than a monitoring dashboard.
Tables scan fast; detail views breathe.

**Elevation**

Flat with clear borders. Avoid heavy shadows — they read poorly in dark mode.

## A4. Internationalization

Multi-language is infrastructure, not content work.

- Language chosen at first-run setup, changeable in **Settings → General**
- All UI strings in locale files. Launch with `en`, `de`, `ar`. More via community PRs.
- **German runs ~30% longer than English.** No fixed-width buttons or labels. Every
  layout must survive 1.4x string expansion without breaking.
- **Arabic requires full RTL.** Mirrored layout, mirrored directional icons (arrows,
  chevrons). But Latin-script data stays LTR: phone numbers, API keys, timestamps,
  logs, code.
- Dates, times, and number formats follow the selected locale.

## A5. Required states

Every screen must be designed in all five:

| State | Requirement |
|---|---|
| Empty | First-run has zero calls. This screen must teach without a manual and offer one clear next action. |
| Loading | Skeletons, not spinners, for lists and detail views. |
| Error | Say what broke and what to do. Never a bare error code. |
| Success | Confirm the action happened; don't leave the user guessing. |
| Offline | The system lost SIP registration or a provider. This must be loud. |

Empty states matter most here — a fresh install is entirely empty.

## A6. Screens

### A6.1 Onboarding — three steps, once

**Step 1 — Connect a number**

Two clearly separated paths, side by side. Do not bury either:

- **I have a number from a provider** — pick the provider, paste the credentials,
  and the number is configured to reach the agent. Documented end to end for the three
  providers in §B3.1. This is the path most people take.
- **I have SIP or a PBX** — host, port, username, password, register (3CX, Asterisk,
  FreePBX). The path for a business that already runs a PBX.

Both end at the same place. Neither is presented as the advanced one.

**Buying a number inside the app comes later** (§B3.1), and buying it *from us* is
Tel-Agent Cloud only. Until then this step must be honest about what it needs: an
account with a provider, and in the EU a regulatory bundle that can take days to
clear. Say so here rather than letting the user discover it after starting.

**Step 2 — Providers**

Three blocks: STT, LLM, TTS. Each has: provider dropdown, API key field, and a
**"Test connection"** button that must return a real result before the user continues.
Show approximate cost per minute.

Offer a **"Use local models"** path (Ollama + Whisper + Piper), clearly labeled as
requiring a GPU. Do not make it the default — a first experience on a CPU-only machine
is a choppy call and a lost user.

**Step 3 — Your agent**

Name, language, voice (with preview playback), and a personality prompt that is
**already filled with a working default**. Never show an empty prompt box.

**Finish — "Call yourself now"**

One large button. This is the single most important element in the product. It is the
moment the user hears the thing work and decides whether to stay. Design it as the
payoff of the entire flow — full width, unmissable.

### A6.2 Home

Top bar: system status (connected / degraded / offline) · calls today · master on-off.

Body, in this priority order:

1. **Needs your attention** — callbacks requested, calls the agent couldn't handle,
   failed tool calls, provider errors. When empty, say so warmly. Never a bare box.
2. **Recent calls** — compact rows, click to open.
3. **Dial card** — small, in a side column. For test and outbound calls. Deliberately
   minor: the user is not a switchboard operator; the agent does the work.

### A6.3 Calls — list

Columns: caller (contact name when known, number below) · time · duration · handling
badge (passed / blocked / AI) · one-line summary.

Filters: date range · handling type · has recording · detected intent.

**Full-text search across all transcripts is the headline feature of this screen.**
Typing "prescription" surfaces every call where it was said. Give the search field real
prominence — not a small icon in a corner. This is the feature that makes people stay.

### A6.4 Call detail — **design this screen first**

This is the heart of the product. It defines the whole design system.

Layout:

- **Header** — caller, contact link, date, duration, how it was handled, detected intent
- **Audio player** — waveform, scrubbable, synced to the transcript (clicking a line
  jumps the audio)
- **Transcript** — speaker labels and timestamps, with clear markers for human takeover:

```
00:03   Caller     I have an appointment Tuesday, can I move it?
00:07   Agent      Of course. What day works for you?
00:14   ──── human joined: Mohamed ────
00:16   Mohamed    Thursday at ten
00:22   ──── agent resumed ────
00:24   Agent      Booked — Thursday at 10:00. Anything else?
```

- **Whisper channel** — operator instructions the caller never heard. Shown in a
  visually separate side channel. **Never inline** with what the caller actually heard.
- **Right rail** — summary, detected intent, and the list of tools the agent actually
  invoked (with result status)

### A6.5 Rules

Three visual columns: **Always through** · **Blocked** · **AI handles**.

- Add a number in one action
- Drag between columns
- Each entry shows when that number last called and how it was handled — rule and
  consequence in one view

Also on this screen: **business hours** (outside them the agent always answers) and
**failover behavior** if a provider fails mid-call.

### A6.6 Agent

- Persona prompt (with the working default pre-filled)
- Language, voice, speaking speed
- **Knowledge sources** — uploaded text or files the agent can search
- **Tools** — each a card with a toggle, a plain-language description of *when* the
  agent will use it, and its config. Show a visible warning when many tools are on:
  every extra tool raises the chance the agent uses one at the wrong moment.
- **"Try it" panel** — a text chat against the same agent, no phone call needed. This
  is the prompt-tuning loop. Make it fast and always reachable.

### A6.7 Live call

Appears whenever a call is active, reachable from anywhere in the app.

- Live transcript, streaming word by word
- Caller info, matched contact, previous interactions
- Three intervention actions, **large and unambiguous** — this screen is used under
  time pressure, mid-conversation:
  - **Whisper** — type an instruction; the agent speaks it in its own voice; the caller
    never knows. *Highest value, lowest complexity. Build first.*
  - **Take over** — agent goes silent. Operator speaks by mic, **or types and the agent
    voices it** (important for users who don't want to speak).
  - **Hand off** — transfer to a real phone and exit.

### A6.8 Settings — one screen, eight tabs

1. **General** — interface language, theme, timezone, agent display name
2. **Providers** — STT / LLM / TTS: provider, key, "Test connection", est. cost/min
3. **Numbers** — connected numbers, add another, per-number agent assignment, status
4. **Channels** (§B13, Milestone 3) — one card per channel: connect, per-channel agent
   assignment, status. Every card holds credentials the customer created in their own
   developer account, so each needs a **"Test connection"** that proves the link rather
   than claiming it, and each shows the saved token **masked to its last four
   characters** — never in full, per §B9. Telegram can register its own webhook from
   this screen; the Meta channels cannot, and their setup steps must be spelled out
   here rather than left to a support article
5. **Routing** — forwarding target, business hours, failover
6. **Integrations** — webhooks in/out (with "Send test"), n8n URL, notification channel
   (Telegram / email), MCP endpoint + token
7. **Privacy & recording** — recording on/off, retention period, **automatic recording
   announcement (ON by default — legally required in Austria, and it must cover human
   takeover too, not only the agent)**, data export, data deletion
8. **System** — version, updates, logs, config export/import, reset

### A6.9 Standalone pages

- **Contacts** — known callers, tags, per-contact history across all channels. Feeds
  agent memory. Shows calls and (later) chats in one timeline.
- **Logs** — technical log stream for debugging. Monospace, filterable, copyable.

## A7. Design deliverable

**Design A6.4 (Call detail) first, alone.**

It is the heart of the product and it will settle color, type, density, card style, and
how speaker labels and intervention markers read. Once it's right, the rest follow
quickly.

Do not design all screens up front.

---

# PART B — TECHNICAL SPECIFICATION

## B1. Stack

| Layer | Choice | Why |
|---|---|---|
| Voice agent | **Python** | `livekit-agents`, and every STT/LLM/TTS SDK ships Python first |
| API | **Python + FastAPI** | Same runtime as the agent; auto-generated OpenAPI docs |
| Frontend | **Next.js + React** | Known stack; SSR not required but harmless |
| Database | **PostgreSQL or SQLite** | Transcripts need real full-text search. Postgres gives it through `tsvector`, SQLite through FTS5; one interface, two implementations, both in CI (D-029) |
| Cache / queue | **Redis** | Session state, rate limiting |
| Reverse proxy | **Caddy** | Automatic Let's Encrypt; also solves the mic-over-HTTPS problem |
| Packaging | **Docker Compose** | Multiple services + heavy audio dependencies |

**Voice pipeline: build on LiveKit Agents (Apache 2.0).** It provides SIP integration,
turn-taking, barge-in, and session management out of the box. Do not rebuild these.
Our value is in routing, rules, archive, and tools — not in reinventing the audio path.

## B2. Repository layout

```
tel-agent/
├── agent/                  # Python — SIP, voice pipeline, agent loop, tools
│   ├── providers/          # stt/, llm/, tts/ — one interface, many implementations
│   ├── tools/              # built-in tool implementations
│   ├── routing/            # whitelist / blacklist / hours logic
│   └── session/            # call lifecycle, turn-taking, whisper handling
├── api/                    # Python FastAPI — REST + WebSocket
├── web/                    # Next.js dashboard
├── locales/                # en / de / ar
├── examples/
│   └── workflows/          # importable n8n JSON examples
├── docker-compose.yml
├── docker-compose.dev.yml
├── CLA.md
├── LICENSE                 # AGPL-3.0
├── IDEAS.md                # parking lot — keeps scope out of v1
└── README.md
```

## B3. Provider interfaces

Every provider sits behind one interface. **Implement exactly one of each for v1** —
the abstraction is written on day one, the second implementation comes after the first
call works.

```
STTProvider   → stream(audio) -> partial/final transcripts
LLMProvider   → stream(messages, tools) -> token stream + tool calls
TTSProvider   → stream(text) -> audio chunks
              → cancel()        # MUST exist: on barge-in, stop instantly
```

`cancel()` is not optional. When the caller interrupts, audio must stop immediately and
the queued speech is discarded. Without it the agent talks over people and the product
feels broken.

**v1 implementations:** Deepgram (STT) · one cloud LLM (fastest available) ·
ElevenLabs (TTS).

**v1.1:** Ollama, Whisper local, Piper, Cartesia — many will arrive as community PRs.

## B3.1 How a number reaches the agent

Number acquisition sits behind an interface for the same reason STT, LLM and TTS do:
so that no single vendor — **including the hosted edition's own** — ends up in the code
that self-hosters run.

```
NumberProvider → list_available(country)      # what can be bought
               → provision(number)            # acquire and configure
               → release(number)
               → inbound_route(number)        # where calls are delivered
```

Four implementations, in this order:

| Implementation | Status | Notes |
|---|---|---|
| **Bring your own number** | v1 | The user's own account at Twilio, Telnyx or similar. Their keys, their number, their bill. The only path in v1. |
| **Bring your own SIP / PBX** | v1 | An extension or trunk on 3CX, Asterisk or FreePBX. The better story for a business that already runs a PBX, and the reason the on-premises media path matters. |
| **Buy in-app** | later | Provisioning through the user's own provider credentials, from inside the UI. Convenience over the first row, not a different relationship. |
| **Resold by Tel-Agent Cloud** | Tel-Agent Cloud only | Numbers held by Dpro GmbH and assigned to customers. **This implementation does not ship in the open edition.** |

### Why reselling stays out of the open edition

Two separate reasons, and either alone is sufficient.

**It is a compliance function, not a feature.** In the EU, provisioning a number
requires a regulatory bundle per country — identity and address documents, and for
geographic numbers usually an address inside that country. Reselling means carrying
that obligation for every customer, plus questions about emergency service access
where the number is somebody's main line, plus being the holder of record when a
customer wants to port a number away. None of this belongs in software a stranger
installs from GitHub.

**The margin is not the point.** A number costs roughly €1–3 per month and inbound
minutes well under a cent, while TTS alone runs €0.06–0.10 per minute. Number resale
therefore earns close to nothing and should never be modelled as a revenue line. Its
value is that it removes an account signup, a document upload and a trunk
configuration from onboarding — a conversion improvement, priced as convenience.
Revenue comes from the subscription and the per-minute AI.

### What the open edition may know

A generic `TwilioProvider` or `TelnyxProvider` into which the user pastes **their own**
credentials. Nothing referencing a Dpro-held account, no billing logic, no subaccount
orchestration. `numbers.provider_account_ref` exists so the hosted edition can map a
row to a provider subaccount; in a self-hosted installation it simply stays null.

### Which providers are documented

"Any SIP trunk" is a support burden disguised as a feature. Pick **three** providers,
test them on real calls, and give each one a setup page. Everything else stays possible
through generic SIP and explicitly unsupported.

## B4. Latency budget — the hard requirement

Target **under 800ms** from end of caller speech to first audio out.

| Stage | Budget |
|---|---|
| Endpointing — deciding the caller has finished | ~200ms |
| STT final | ~100ms |
| LLM first token | ~250ms |
| TTS first chunk | ~100ms |
| Network / buffer | ~150ms |

**Endpointing is inside the budget, and it is usually the largest stage.** The metric
is measured from the end of caller speech, so the time spent deciding that the caller
has finished is spent inside it. A plain silence threshold of 500–800ms therefore
consumes the whole budget before the first provider is called, which makes semantic
turn detection a requirement rather than a refinement.

These figures are an allocation, not a measurement. Replace them with real numbers
from the first calls, and treat the endpointing row as the one most likely to be wrong.

**Everything streams.** The first sentence starts speaking while the rest is still being
generated. Never wait for a complete LLM response before starting TTS. This single
decision is the difference between a natural call and an obviously robotic one.

**Tool latency must be covered by speech.** If a tool takes 3 seconds, the agent says
"one moment, let me check the calendar" and runs the call in parallel. Silence reads as
a dropped call.

## B5. Data model (core tables)

```
users            id, username, email, password_hash, locale, theme, created_at
workspaces       id, name, created_at
memberships      id, user_id, workspace_id, role(owner|admin|reception|viewer|invited)
apps             id, slug, origin(official|community|planned|mcp), version, manifest
app_installs     id, workspace_id, app_id, enabled, settings_json

channels         id, workspace_id, kind, app_id,
                 name, credentials_encrypted, webhook_secret, webhook_path,
                 default_language, agent_id, status
numbers          id, workspace_id, channel_id, provider, provider_account_ref,
                 owner(customer|platform), e164, sip_config, agent_id, status
agents           id, workspace_id, name, persona_prompt, language, voice_id, settings
contacts         id, workspace_id, e164, name, tags, notes
rules            id, workspace_id, e164_or_pattern, action(pass|block|ai), note

conversations    id, workspace_id, channel_id, contact_id, external_id, direction,
                 started_at, ended_at, handling, intent, summary, state_json, status
calls            conversation_id, number_id, from_e164, recording_path,
                 billable_seconds, provider_cost_micros
messages         id, conversation_id, ts_ms, speaker(caller|agent|human), text,
                 is_whisper, stt_confidence, language

tool_invocations id, conversation_id, tool_name, args, result, status, latency_ms
knowledge        id, workspace_id, agent_id, title, content, embedding
webhooks         id, workspace_id, url, events[], secret
```

**`conversations` is the core table, not `calls`.** A phone call is a conversation
whose channel is of kind `phone`; it additionally has a row in `calls` carrying the
things only a phone call has — the caller's number, the recording, the billable
seconds and the provider cost. A WhatsApp thread is the same conversation row with a
different channel and no `calls` row.

Everything built for the phone therefore works on every channel without a branch:
full-text search, the archive, routing rules, `take_message`, tool invocations, the
live view. That is the entire reason for the split.

**Six decisions that are painful to add later — make them now:**

1. **`workspace_id` on every table from day one — D-028.** This decision originally
   read "`user_id` on every table, even while it is always `1`"; the reasoning below is
   unchanged, only the key is. The interface is built on workspaces and says so in its
   own copy — *"A workspace is a separate installation: its own numbers, assistants,
   catalogue and call history. Nothing crosses between them."* Superseded text: adding
   multi-tenancy to a live database later is real pain, and the hosted edition needs it.
2. **Full-text index on `messages.text`** from the first migration. §A6.3 calls
   transcript search the headline feature of the calls list; an index added later means
   the data was all there and the feature was not.
3. **`numbers.owner`** — is the customer the holder of record for this number, or is
   the platform? This is the column that separates a self-hoster's own Twilio number
   from a number resold by Tel-Agent Cloud, and it governs who may release or port it.
   Backfilling it once both kinds exist means guessing.
4. **`calls.billable_seconds` and `calls.provider_cost_micros`** — usage metering from
   the first stored call. Two columns today; without them, any later per-minute pricing
   starts with no history to bill or reconcile against. Store cost in integer micros,
   never floats.
5. **`messages.stt_confidence` and `.language`** — per line, not per call.
   Rule 4 requires 20 real calls in Austrian German before trusting an STT provider,
   with names and addresses as the known failure point. Stored per line, confidence
   turns that from replaying recordings into a query — *show every line under 0.7* —
   and the failure pattern surfaces on its own. Without it, "German accuracy" stays an
   impression rather than a measurement. `language` matters once `en` / `de` / `ar`
   coexist and a caller switches mid-conversation. Both are null on text channels,
   which is correct: typed text has no recognition confidence, and that null is itself
   the signal that the line was typed rather than spoken.
6. **`conversations` as the core table, with `calls` as a phone-only extension.**
   The product answers on ten channels (§B13), so a schema whose master table is named
   `calls` and whose lines are keyed by `call_id` is wrong from the first migration.
   Renaming today costs nothing — there is no code and no stored row. Renaming after
   Milestone 2 means migrating every transcript, every query, every API path and every
   screen. The `channels` table exists from the first migration too, holding exactly
   one row of kind `web` until Milestone 3 — the same discipline as `user_id` being
   permanently `1`, and for the same reason: the structure is what makes the later work
   a write instead of a redesign.

## B6. API surface

REST, documented automatically by FastAPI. The dashboard consumes this same API —
so it exists anyway; just make it public and documented.

```
GET    /health                        # deep check: SIP reg, providers, DB

# Authentication — D-030. No public signup: the install wizard creates the
# administrator, and further users are invited by an administrator.
POST   /api/auth/login                # username + password -> cookie session
POST   /api/auth/logout               # this session
POST   /api/auth/logout-all           # every session on the account
GET    /api/auth/me                   # current user, memberships, active workspace
POST   /api/auth/code/verify          # the six-digit code: reset and second factor
POST   /api/auth/forgot               # username in; answer never reveals existence
POST   /api/auth/password             # change; invalidates every other session
GET    /api/auth/key/challenge        # SSH challenge, two minutes, single use
POST   /api/auth/key/verify           # signature in, no key ever leaves the client
POST   /api/setup                     # first run only: create the administrator

# Web chat's widget — the only unauthenticated routes in the product. Guarded by
# origin allowlist, captcha and rate limit rather than by a session. See §B14; the
# rule there is that every response must be safe to show a stranger.
POST   /public/chat/{path}/messages   # a visitor's message; creates the conversation
GET    /public/chat/{path}/stream     # the reply, token by token

# Workspaces — D-028. Every route below is scoped by the active workspace.
GET    /api/workspaces                # the ones this user is a member of
POST   /api/workspaces                # create, optionally seeded from another
GET    /api/workspaces/{id}/members
POST   /api/workspaces/{id}/invites

# Extensions — D-031
GET    /api/apps                      # the catalogue, with install state
POST   /api/apps/{slug}/install
POST   /api/apps/{slug}/enable        # and /disable

GET    /api/home                      # the two counts §A6.2 opens with
GET    /api/catalogue                 # services and the workspace's currency
POST   /api/catalogue   PATCH /api/catalogue/{id}   DELETE /api/catalogue/{id}

GET    /api/conversations             # list + filter, every channel
GET    /api/conversations/{id}        # detail + messages
GET    /api/conversations/search?q=   # full-text across every channel
POST   /api/calls/outbound            # {to, prompt} — phone only, starts a real call
GET    /api/rules  POST  /api/rules
GET    /api/agents PATCH /api/agents/{id}
GET    /api/contacts
GET    /api/channels   POST /api/channels     # connect a channel (§B13, Milestone 3)
GET    /api/settings PATCH /api/settings
POST   /api/providers/test            # test connection
WS     /ws/conversations/{id}         # live transcript / message stream
WS     /ws/conversations/{id}/whisper # operator → agent, mid-conversation
```

The paths follow the data model: everything that is not phone-specific is a
*conversation*. `POST /api/calls/outbound` keeps its name because it does something
only a phone can do — place a call — and because §B9.1 treats it as one of the three
paths that spend real money.

**Webhooks out:** `call.started` · `call.ended` · `intent.detected` ·
`message.taken` · `tool.failed` · `system.degraded`

Each signed with a shared secret.

**The names above are the phone-era set and are superseded in code.** §B5 decision 6
made a call a conversation on a `phone` channel, so `api/models/webhook.py` sends
`conversation.started`, `conversation.ended`, `message.received`, `assistant.changed`
and `knowledge.changed`. This list should be rewritten against that when somebody
settles what the remaining four become.

### How a receiver verifies one

Every delivery carries four headers:

| Header | |
|---|---|
| `X-Tel-Agent-Event` | the event name |
| `X-Tel-Agent-Timestamp` | unix seconds, and part of what is signed |
| `X-Tel-Agent-Signature` | `sha256=` followed by the hex HMAC |
| `X-Tel-Agent-Delivery` | the delivery's id — **keep it and ignore repeats** |

The signature is `HMAC-SHA256(secret, f"{timestamp}.{raw_body}")`, hex. Two things about
that string matter and both are deliberate:

- **The timestamp is inside it.** Signing the body alone means a delivery captured once
  can be replayed for as long as the secret lives, because the body has not changed.
  Refuse anything whose timestamp is more than a few minutes old.
- **`raw_body` is the bytes as they arrived**, before any parse. A framework that
  re-serialises the JSON for you produces a different byte string, and the signature
  over it will not match no matter how correct the code looks.

```python
import hashlib, hmac, time

def verify(secret: str, headers, raw_body: bytes, tolerance: int = 300) -> bool:
    timestamp = int(headers["X-Tel-Agent-Timestamp"])
    if abs(time.time() - timestamp) > tolerance:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    # Constant time: a plain `==` leaks how much of the signature was right.
    return hmac.compare_digest(expected, headers["X-Tel-Agent-Signature"])
```

**Delivery is at least once.** A receiver that is briefly unreachable is retried with
backoff, so the same event can arrive twice — act on `X-Tel-Agent-Delivery` once and
ignore a repeat. **Redirects are not followed**: a signed POST that follows one delivers
the signature somewhere the operator never registered.

**Webhook in:** `POST /hooks/call` — start an outbound call from n8n or anything else.

## B7. Built-in tools

Keep this list short. Five precise tools beat twenty that confuse the model.

| Tool | Purpose |
|---|---|
| `transfer_call` | Hand to a human |
| `take_message` | Structured message capture |
| `end_call` | Polite close |
| `send_notification` | Telegram / email |
| `http_request` | Generic escape hatch — covers everything else |
| `search_knowledge` | Search user-uploaded content. **Will be the most used tool.** |
| `check_calendar` | Google Calendar + CalDAV (covers Nextcloud, iCloud) |

**Calendar rule:** the agent **proposes and confirms**, or writes to a review calendar.
It does not book directly into a live calendar in v1. One wrong entry destroys trust
permanently.

## B8. Health and monitoring

Not a feature — a requirement. A silently dead phone service is worse than an obviously
dead one, because the user only finds out after losing ten calls.

- `/health` checks **actual** state: SIP registration, provider reachability, DB
- Alert immediately (Telegram / email) on lost SIP registration
- Clear status indicator in the dashboard top bar
- Per-call telemetry: latency per stage, tool timings, interruption count

## B9. Security

- **Password required on first run.** Not optional, no default credentials.
- **Encrypt API keys at rest.** Never return them in full to the client after saving.
- **Do not expose the port to the internet by default.** Document VPN / Tailscale /
  reverse-proxy-with-HTTPS as the supported paths.
- **Browsers block microphone access over plain HTTP outside `localhost`.** Voice
  takeover therefore requires HTTPS. Caddy solves this; until then, ship whisper and
  type-to-speak only.
- **MCP endpoint needs its own token**, separate from the dashboard session, plus hard
  limits (calls per hour, allowed destination numbers). An external model that can
  start real calls spends real money.
- **Recording announcement on by default.** Austria requires both parties to be aware,
  and the requirement rises once a human joins.

## B9.1 Outbound abuse prevention

**This applies to every installation, not just the hosted edition.** Anyone running
Tel-Agent pays for the calls it makes.

**Toll fraud / IRSF** is the attack: someone reaches an outbound path and makes the
system call premium-rate numbers they control. The operator pays for every minute, and
those minutes are expensive by design. There is no single fix — the defences below are
layered, and the outer ones must hold even when the inner ones have a bug.

Tel-Agent exposes **three** paths that can start a real call, and all three need the
same treatment:

| Path | Control |
|---|---|
| `POST /api/calls/outbound` | Authenticated dashboard session |
| `POST /hooks/call` | Its own token — **not** the outbound-webhook signing secret — rotatable |
| MCP endpoint | Its own token, separate from the dashboard session |

Each path gets a separate credential. A leak of one must not open the others.

### Allow what is known; do not block what is not

A blocklist always loses — an attacker has fresh numbers every day. Default policy:

```
An outbound call is permitted only if the destination is:
  - an existing contact, or
  - a number that has previously called this installation, or
  - explicitly approved by the operator
and its country is on the allowed-countries list.
```

This costs almost nothing in practice, because Tel-Agent's real outbound use is calling
someone back or dialling a known contact. It removes most of the attack surface.

**Always refused, regardless of other rules:** `+882` and `+883` (international
networks) and `+881` (satellite). These have no legitimate use for a normal business
line and cost tens of currency units per minute.

### Limits, enforced in `agent/`

Calls per hour per account · spend per day · maximum single-call duration · repeat
calls to the same number per hour.

**These are checked before the call is placed, in the agent — not in billing.** Billing
finds out afterwards, and the operator pays for the interval.

### Detection

Alert via `send_notification` on: a sudden rise in outbound volume · calls outside
business hours · destinations never dialled before · unusually long calls (fraud earns
per minute, so it stalls) · the same prefix repeating quickly.

Alerting alone is not enough — crossing a spend limit must **stop placing calls**, not
just send a message. The master off switch (§A6.2) is the manual version of the same
thing.

### At the provider, and at the PBX

Configure geographic dialling permissions in the telephony provider's own console and
disable every country the installation does not need. This defence sits outside our
code and survives our bugs, so it is the most valuable one. It is **not** enabled by
default on a new account — it has to be set deliberately.

On the PBX: use a strong extension password, do not expose SIP to the internet, and
disable outbound calling on the agent's extension entirely while the agent only
answers.

## B9.2 Where credentials live — `.env` or database

Two kinds of secret, two homes. Confusing them is why this section exists.

| | `.env` | Database, encrypted |
|---|---|---|
| What | Installation secrets | Credentials the user enters |
| Examples | `DATABASE_URL`, `REDIS_URL`, **`ENCRYPTION_KEY`**, LiveKit keys at Milestone 11 | STT / LLM / TTS API keys, SIP credentials per number, channel tokens (§B13) |
| Set by | Whoever runs the server, once | The user, from the UI, at any time |
| How many | Exactly one set per installation | Many — several numbers, five channels, one key per provider |
| Changing it | Restart | Takes effect immediately |

**The bridge between them:** `ENCRYPTION_KEY` lives in `.env` and is what encrypts the
credential columns in the database. Lose it and every stored credential is
unrecoverable; leak it and encryption at rest bought nothing.

### Why channel tokens must not go in `.env`

Four reasons, and the first alone settles it:

1. **They are entered from the UI.** §A6.8 tab 4 connects a channel by pasting a token
   and pressing *Test connection*. Nothing in that flow can edit a file on the server
   and restart the process.
2. **There can be more than one of each.** Two Telegram bots, two WhatsApp numbers, a
   separate agent per channel — environment variables have no natural plural.
3. **OAuth tokens are refreshed.** A `.env` that rewrites itself while the process runs
   is a class of bug nobody wants.
4. **The hosted edition needs one row per tenant.** A single-valued file cannot express
   that, and retrofitting it later means moving every stored credential.

So: **the credential goes in the database, encrypted; the key that encrypts it goes in
`.env`.** This is also what §B9 already requires — encrypted at rest, never returned in
full to the client, shown in the UI only as a masked preview with the last four
characters visible.

### Milestone 0 is the exception, and only until Milestone 1

There is no database in Milestone 0, so everything is in `.env` — including the
provider keys. That is correct for one script and one page, and wrong for anything
with a dashboard. The move happens at Milestone 1, when persistence arrives; do not build a
settings screen that writes to `.env`.

**Unchanged in all cases:** never log a credential, never commit one, never return one
in full to a client, and `.env.example` documents every variable with a safe
placeholder.

## B10. Deployment

```bash
docker compose up -d          # everything
docker compose --profile automation up -d   # + bundled n8n (optional)
```

- Also document **manual dev run** (`pip install` + `npm run dev`). Contributors need
  to run code without rebuilding an image on every edit.
- **Never assume Docker in code.** All config from environment variables.
- Settings export/import so a user can test locally and move to a server with one file.
- On a server: HTTPS via Caddy, and open a UDP range for RTP (e.g. 10000–20000) with
  `external_ip` configured. Most "call connects but no audio" reports trace to this —
  document it prominently.

## B11. Build order

Do not start step N+1 before step N works.

| # | Milestone | Done when |
|---|---|---|
| 0 | **Web chat** | A visitor types, the model replies token by token, the reply can be cancelled mid-sentence, a message is taken and a transcript printed. **No dashboard, no Docker, no database.** |
| 1 | Persistence | Postgres, conversations + messages stored, schema per §B5 |
| 2 | Web UI | Conversation detail → conversations list → home → rules → agent → settings |
| 3 | Messaging channels | The nine in §B13, same agent and tools, different transport |
| 4 | Routing rules | Pass / block / AI, from a channel identity rather than a phone number |
| 5 | Tools | The seven in B7 |
| 6 | Webhooks + REST | Documented, signed |
| 7 | MCP server | Thin layer over the REST API, with hard limits |
| 8 | Live intervention | Whisper first, takeover after |
| 9 | Health + alerts | B8 complete |
| 10 | Docker packaging | One-command install |
| 11 | **Phone call** | A provider number rings, the agent answers, speaks via TTS, takes a message, and the transcript lands in the same archive as every chat. Brings the STT and TTS interfaces. |

**Milestone 11 sits last, and it is the one that judges the ten before it.** The phone
is the hard case — no interface, no way to show the caller what was understood,
sub-second latency, and interruption mid-sentence. Text channels are forgiving and
will let a slow architecture look healthy, which is exactly the trap this order walks
into on purpose: the defence is that every interface built before it streams and
cancels, and Milestone 0 proves both while they are cheap.

**Milestone 0 is the only one that matters right now.** If it works within two weeks,
everything above is worth building. If it doesn't, that is valuable information gained
cheaply.

## B12. Licensing and ownership

- **AGPL-3.0.** Anyone running it as a network service must publish their modifications.
  This is what makes a future commercial license sellable.
- **CLA required from the first contributor.** A simple agreement, signed electronically
  via a GitHub bot, granting relicensing rights. **Add it before the first PR** — after
  that it becomes practically impossible, and without it the commercial license option
  is gone forever.
- **Copyright held by Dpro GmbH.** Any code shared with other projects needs a written
  license arrangement between the entities — cheap now, expensive later.
- Three revenue paths, one codebase: **hosted edition** · **commercial license for
  closed-source integration** · **support**. The free version is never crippled; it is
  the product.

*Not legal advice — confirm the trademark position on "Tel-Agent" (EUIPO classes 9 and
42) before committing to a logo. An older academic dialogue-systems framework shares
the name.*

### B12.1 What AGPL does and does not do

AGPL cannot restrict what users do with the software. An open license means full
freedom of use. The only enforceable obligation is AGPL's own: modify it and run it as
a network service, and you must publish your modifications.

*Commercial and ownership strategy is maintained privately and is not part of this
specification.*

## B13. Messaging channels — Milestone 3

Nine channels alongside the phone: **web chat · SMS · email · WhatsApp · Telegram ·
Messenger · Instagram · Discord · Slack**. The same agent, the same tools, the same transcript
archive; a different transport.

**Three of them need no platform at all**, and they come first:

| Channel | What it needs |
|---|---|
| **Web chat** | A script tag on the customer's own site. No account, no review, no approval by anyone. The easiest channel in the product — and the only one with a public endpoint, which §B14 is about. |
| **SMS** | Nothing new — it arrives with the telephony account the phone number already uses. |
| **Email** | An IMAP/SMTP mailbox the customer already owns. |

The remaining six each require an application in the customer's own developer account
on that platform, and several require review before they can message the public.
That review is the slow part, not the code.

**The line that keeps the list closed:** a channel is a route a **customer** uses to
reach a business. It is not a system the business itself runs on — Teams and project
trackers are integrations, reached through the HTTP tool. Slack sits on the channel
side of that line for one case and not the other: an outside customer in a shared
channel with a supplier is a route in, an internal workspace is not. Without that
line, "add one more connector" has no end, which is the failure §Rule 5 exists to
prevent.

**The list is closed.** Ten channels total including the phone. Adding an eleventh is
a decision to reopen this section, not a pull request.

### The customer connects their own app

Every channel stores per-tenant credentials that the customer creates in **their own**
developer account. Tel-Agent never holds a shared platform application.

This is the same reasoning as §B3.1 for phone numbers, and it is not only about
philosophy: one shared app puts every installation behind one rate limit, and makes a
single policy violation everybody's outage. It is also what keeps Tel-Agent installable
by a stranger from GitHub with no account at Dpro.

Token handling follows §B9 exactly — encrypted at rest, excluded from every API
response, and surfaced to the UI only as a masked preview showing the last four
characters, so the settings screen can show *which* credential is saved without
revealing it.

Per-channel credential fields, how the customer obtains them, webhook wiring and the
platform limits that shape design are in `internal/CHANNELS-REFERENCE.md`.

### What is shared and what is not

| | Shared with the phone | Channel-specific |
|---|---|---|
| Agent persona, knowledge, tools | ✅ | |
| Transcript storage and search | ✅ | |
| Routing rules, business hours | ✅ | |
| Structured capture (§B7 `take_message`) | ✅ | |
| Transport, credentials, webhooks | | ✅ |
| Rich replies — buttons, lists, forms | | ✅ |
| Turn-taking, barge-in, endpointing, streaming | **phone only** | |

The last row is the important one. Every text channel shares one property that makes
it easy: **the user waits.** A two-second pause in a chat is invisible; the same pause
on a call sounds like the line dropped. Nothing in the audio path has an equivalent in
the text channels, and nothing written for the text channels is fast enough for voice.

### How much conversation machinery each channel needs

Decided by how much interface the platform provides, not by preference:

| Channel | Interface offered | State needed |
|---|---|---|
| Discord | Native modal — a real form | None. One submission arrives complete |
| Telegram | Inline keyboards, Mini Apps | Light |
| WhatsApp / Messenger / Instagram | Buttons and list menus | Full step machine |
| **Phone** | **Nothing at all** | **Full step machine, plus interruption** |

The phone is the extreme case of this scale, which is why it is built first and the
rest fitted to it. See §B11.

### Data model

No new tables are needed at Milestone 11. `channels`, `conversations` and `messages`
are created in the first migration (§B5) precisely so that this milestone is a matter
of inserting channel rows and writing transports, not of migrating a year of stored
transcripts.

Until Milestone 11 the `channels` table holds a single row of kind `phone`. That row
looks like waste and is not: it is what makes the difference between adding a channel
and rebuilding the schema underneath a live product.

## B14. Web chat — the embed, and what protects it

§B13 calls web chat the easiest channel in the product, and it is: no platform account,
no review, nobody's approval. That ease is also the danger. It is the only channel whose
endpoint is **public and unauthenticated** — anybody who finds the URL can post to it,
and unlike a webhook there is no provider signature to check.

### What the customer pastes

One script tag, from the settings screen:

```html
<script src="https://telagent.example/embed.js" data-tel-agent="c7f2…"></script>
```

The script's only job is to create an **iframe** pointing at the widget, and to size and
position it. Everything the visitor sees and types lives inside that iframe.

The iframe is the point, not an implementation detail. It isolates in both directions:

- **Their page cannot read the conversation.** A visitor typing a medical complaint into
  a chat bubble is not typing it into the site's analytics.
- **Our widget cannot read their page.** Nothing in Tel-Agent can reach the host page's
  DOM, its cookies, or a half-filled checkout form — so an installation cannot become a
  liability for the site that installed it.
- **Their CSS cannot break it, and ours cannot break theirs.** A script-injected div
  inherits whatever the host page does to `div`, which is how embedded widgets end up
  unreadable on one customer's site and fine on every other.

A `<script>` that injected markup directly would be faster to write and would give up all
three.

**On Subresource Integrity.** `embed.js` is served by the same installation that answers
the chat, so pinning its hash guards against nothing a compromise of that installation
would not already own. It also cannot be pinned: the file changes with every upgrade, and
a stale `integrity` would silently stop every customer's widget on the day they update.
Tel-Agent Cloud is the case where the script does cross an origin, and there it is served
from a versioned URL so the hash and the file change together.

### The identifier in the tag

`data-tel-agent` is the channel's `webhook_path` (§B5) — long, random, and unique across
the installation. It is not the workspace id, and not a guessable slug.

It is not a secret: it travels in the HTML of a public page and anybody can read it. It
is an **address**, and what protects the address is the next section. Treating it as a
secret is the mistake to avoid, because it would mean the guard is a value printed on
every page that uses it.

### The allowlist is enforced by `frame-ancestors`, not by the `Origin` header

This paragraph replaces an earlier one that had it wrong, and the mistake is worth
keeping because it is easy to make twice.

The widget runs in an iframe **served by this installation**. When it posts a message
the browser stamps the request with the *iframe's* origin - `https://telagent.example` -
and never with the site the iframe is embedded in. An allowlist of customer sites
compared against that header therefore matches nothing, on every page including the
allowed ones. The check reads like a guard and refuses everybody.

The embedding decision has to be enforced where the embedding happens: when the browser
decides whether to render this document inside somebody's page. That is
`Content-Security-Policy: frame-ancestors`, sent on the widget page and built from the
same list. A browser obeys it, and unlike a header comparison there is nothing for a
page to forge - the attacking page never gets to send anything.

So the list does two jobs, and this is the important half:

- **On the widget page, as `frame-ancestors`** - who may embed it at all. An empty list
  renders `'none'`, and so does an unknown or switched-off address.
- **On the message, as an `Origin` check** that also accepts the installation's own
  origin. Accepting it gives a browser nothing: a page on `evil.test` cannot send
  `Origin: https://telagent.example`, because the browser writes that header and scripts
  cannot. A client that is not a browser can send anything, and the guards for that are
  the rate limit and the captcha - which an origin check was never going to be.

### The origin allowlist is the guard

Each web channel stores the origins allowed to embed it. A request whose `Origin` is not
on that list is refused before anything is stored.

Without it, the address in the tag is enough for any site to embed somebody else's widget
and spend their model budget — and the transcripts of those conversations would land in a
stranger's archive, which is worse than the cost.

- Origins only: scheme, host, optional port. No path, no wildcards in the host.
- An empty list means the channel is off, not open. The safe reading of "not configured
  yet" is the one that refuses.
- `Origin` is set by the browser and cannot be forged by page script. It is absent on
  non-browser requests, and absent is refused: a widget request always comes from a page.

### reCAPTCHA v3 is the second layer, and only the second

The origin check stops other sites. It does not stop a bot on an allowed site, which is
the case that costs money — an unauthenticated endpoint that reaches a paid model is
worth automating against.

reCAPTCHA v3 scores without a puzzle, so a real visitor is never asked to identify a
bicycle. The site key is public; **the secret key is a credential** and lives in an
encrypted column (§B9.2), never in `.env`, never returned to a client.

Its limits, stated so nobody treats it as the wall:

- It is Google's, and it sees the visitor. An installation that will not accept that must
  be able to turn it off — a self-hosted product cannot make a third party mandatory.
- A score is a probability, not a verdict. The threshold is configurable, and the failure
  mode of a high one is refusing real customers.
- With it off, the rate limit below is the only thing left, and that is a deliberate
  choice the operator makes rather than a gap.

**"Could not ask" and "asked and was told no" are different answers.** A network failure
means Google was unreachable, which on a self-hosted box behind a firewall that nobody
opened is ordinary - and taking a business's chat offline for it would be a refusal
caused by nothing the visitor did. That case **allows**, and logs at warning so the
operator finds out from their own log rather than from a bill. A verified low score is
Google answering, and that refuses. Getting the two the wrong way round is the failure
worth naming: one unplugs the guard with a cable, the other closes the shop.

**The token carries an action name, and it is checked.** reCAPTCHA issues a token for
one action; without the check, a token minted by the same site's sign-up form would pass
here. A `success` with no `score` is v2's answer shape, and is refused rather than
compared against a threshold it does not have.

**The three checks run cheapest first**: origin, then the rate limit, then this. A
request that fails either local check must not also cost a round trip to somebody
else's network.

### Rate limits, which are not optional

Per conversation and per origin, enforced whether or not reCAPTCHA is on. A public
endpoint without them is a bill waiting to happen, and the operator finds out from the
model vendor rather than from us.

### What the endpoint may do

```
POST /public/chat/{path}/messages     # no session; Origin + captcha + rate limit
GET  /public/chat/{path}/stream       # the reply, token by token (Milestone 0 step 5)
```

It creates a conversation and stores a visitor message. It may not read another
conversation, list anything, or return workspace data. The widget's whole view of the
installation is the thread it is having, and a bug that widened that would be a leak of
other people's conversations.

Nothing about this endpoint is behind a session, so **every response it can produce must
be safe to show a stranger**. That is the sentence to re-read before adding a field to it.

---

# PART C — Notes for whoever builds this

- The demo is easy. A pipeline of STT → LLM → TTS over SIP can be running in two days.
  The product is hard, and the distance between the two is turn-taking, latency, and
  reliability. Budget accordingly.
- Test every provider **in German** before adopting it. Quality drops noticeably outside
  English, and Austrian pronunciation of names and addresses is where most STT fails.
- 8kHz phone audio is not studio audio. Test with real calls from real phones early.
- Distribution matters as much as code: a strong README, a 30-second video of a real
  call, and launches on Hacker News and r/selfhosted. An excellent project nobody finds
  is a dead project.
- An **n8n community node** for Tel-Agent is one of the strongest distribution channels
  available — a large community actively looking for new nodes.
- Everything discussed but not in this document belongs in `IDEAS.md`. It will still be
  there when it's needed, and it won't distract now.
