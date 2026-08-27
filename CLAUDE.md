# CLAUDE.md — working rules for Tel-Agent

Guidance for Claude Code (and any AI assistant) working in this repository.
Read this before touching anything.

---

## Rule 0 — Everything in the repository is English

**All code, comments, docstrings, identifiers, commit messages, and documentation
are written in English.** No exceptions, regardless of the language used in
conversation.

The maintainer communicates in Arabic; the codebase does not. Tel-Agent is a
public AGPL-3.0 project aimed at an international contributor base, and an
Arabic codebase would close the door on outside contributions.

User-facing *interface strings* are a separate matter: those live in
`locales/` and are translated to `en` / `de` / `ar` (see §A4 of the spec).

---

## Rule 1 — Web chat is the first channel; the phone is the last

**Revised 2026-08-22 by D-017.** The original Rule 1 required an answered phone call
before anything else was built. That order is reversed. The superseded text is kept in
`internal/DECISIONS.md`, along with the cost of reversing it.

Milestone 0 is now:

> A visitor types in a web chat, an LLM replies token by token, the reply can be
> interrupted mid-sentence, and the conversation is stored and searchable.

Then the messaging and social channels. **The phone is built last.**

The phone remains the hard case — sub-second latency, barge-in, and no interface to
show what was understood — so the conversation layer is written to its constraints from
the first line even though web chat does not need them. See Rule 3; none of it is
optional. Skipping it means rewriting the layer when the phone arrives, which is exactly
what the original rule existed to prevent.

**What this does not license.** The scope table in Rule 5 still holds, and everything
outside it still goes to `IDEAS.md`.

---

## Rule 2 — Verify each step before moving to the next

Build order inside Milestone 0. Do not start step N+1 before step N works:

| # | Check | How you know it works |
|---|---|---|
| 1 | The message reaches us | A message typed in a browser arrives at the agent process |
| 2 | Answer it | It replies with a hardcoded greeting |
| 3 | Understand it | The model's reply appears in the page, in the visitor's language |
| 4 | Full loop | Visitor writes → LLM replies → visitor writes again, thread intact |
| 5 | Stream and cancel | Tokens appear as produced, and stop instantly when cancelled |
| 6 | Take a message | It asks for name and reason, prints a structured result |

Steps 1–2 are plumbing. Step 4 is the product, and step 5 is what protects the phone
at Milestone 11: an agent that composes a whole answer before sending it can never be
put on a call.

**Before any code at all:** buy a number, point it at the SIP endpoint, call it from
a mobile, and confirm in the provider console that the call arrives. If it does not,
the problem is in the number configuration and no amount of Python will fix it.

---

## Rule 3 — Everything streams

Target: **under 800 ms** from end of caller speech to first audio out.

| Stage | Budget |
|---|---|
| Endpointing — deciding the caller has finished | ~200 ms |
| STT final | ~100 ms |
| LLM first token | ~250 ms |
| TTS first chunk | ~100 ms |
| Network / buffer | ~150 ms |

**Endpointing is in the budget and is usually the largest single stage.** A plain
silence threshold of 500–800 ms consumes the entire budget on its own, so semantic
turn detection is required, not optional. A budget that omits this stage is not a
budget — measure it separately from the first call.

The first sentence starts speaking while the rest is still being generated.
**Never wait for a complete LLM response before starting TTS.** This one
decision is the difference between a natural call and an obviously robotic one.

`cancel()` on the TTS provider is not optional. When the caller interrupts,
audio stops immediately and queued speech is discarded. Without it the agent
talks over people and the product feels broken.

Tool latency must be covered by speech: if a tool takes 3 seconds, the agent
says "one moment, let me check the calendar" and runs the call in parallel.
Silence reads as a dropped call.

**If latency is above ~1.5 s, do not add features.** Fix the streaming first.

---

## Rule 4 — Measure from the first call

Log these on every call, starting with the very first one:

- Time from end of speech to first audio out
- Where the time goes: STT final / LLM first token / TTS first chunk
- Interruption handling — what happens when the caller talks over the agent
- German accuracy — at least 20 real calls in Austrian German before trusting
  any STT provider, including names and addresses, which is where it fails

---

## Rule 5 — Scope discipline

Every good idea that arrives mid-build goes into `IDEAS.md`, not into the code.
That file is the mechanism that gets this project finished.

| Tel-Agent owns | Tel-Agent does NOT own |
|---|---|
| Telephony / SIP | General workflow automation |
| Voice pipeline (STT → LLM → TTS) | Integrations with 400 SaaS apps |
| Turn-taking, barge-in | Being a CRM |
| Conversation + memory | Being a PBX replacement |
| Call routing rules | Analog hardware support |
| Transcript archive + search | |
| Tool execution | |
| **Messaging channels** — web chat, SMS, email, WhatsApp, Telegram, Messenger, Instagram, Discord, Slack | |

Anything outside the left column is reached through webhooks and the generic
HTTP tool. n8n and Home Assistant do that job better than we would.

**The line between a channel and an integration.** A **channel** is where the
conversation happens — the caller or customer is on the other end of it, speaking or
typing. An **integration** is a system the agent acts *on* while that conversation
runs. Tel-Agent owns channels and reaches integrations through the HTTP tool. Without
this line, "add one more connector" has no end, which is the failure Rule 5 exists to
prevent.

**Channel build order — reversed 2026-08-22 by D-017.** Web chat first, then the
messaging and social channels, then the phone. This paragraph previously said the
opposite; the superseded text and the reasoning are in `internal/DECISIONS.md`.

The phone is still the hard case — it is the only channel with no interface, no way to
show what was understood, sub-second latency and a caller who interrupts mid-sentence.
Text channels are forgiving, and code written to satisfy them is too slow for voice.
Because the phone now comes last, the conversation layer must be written to the voice
constraints from the start and the text channels fitted to *it*, not the reverse.
Setup details per channel are in `internal/CHANNELS-REFERENCE.md`.

---

## Locked decisions

Settled. Do not reopen without a concrete reason.

| Decision | Choice |
|---|---|
| Name | **Tel-Agent** — hosted edition is "Tel-Agent Cloud" |
| Domain | `tel-agent.com` |
| License | AGPL-3.0 + CLA from the first contributor |
| Copyright holder | Dpro GmbH (Vienna) |
| Separate from | Agent-Player and Flowxtra — own repo, no shared code without a written arrangement |
| Backend | Python (agent + FastAPI) |
| Frontend | Next.js |
| Database | PostgreSQL |
| Voice framework | LiveKit Agents |
| Packaging | Docker Compose (manual dev run also documented) |
| Runs as | Locally installed web app on the LAN — not a desktop app, not SaaS-only |
| First test bed | A number from a SIP provider, pointed at the agent |
| Number acquisition | Users bring their own number in v1. Reselling numbers belongs to Tel-Agent Cloud and never enters the open edition |
| SIP at Milestone 11 | LiveKit Cloud SIP |
| Theme | Dark and light, dark designed first |
| Languages | en / de / ar from day one, RTL supported |
| Analog lines | Out of scope — users bridge with an ATA; we only ever speak SIP |
| Workflow automation | Out of scope — webhooks + generic HTTP tool; n8n does the rest |
| Messaging channels | In scope. **Web chat is the first channel built (D-017).** Ten including the phone: web chat, SMS, email, WhatsApp, Telegram, Messenger, Instagram, Discord, Slack. Closed list. The customer connects their own app credentials; Tel-Agent never holds a shared platform app |

---

## How SIP is handled at Milestone 11 — decided

**LiveKit Cloud SIP.** A number from a SIP provider points at a LiveKit Cloud SIP
trunk; the agent connects to the room. Nothing runs locally except the one script.

The objection to this option used to be that media leaves the LAN. That objection
was written when the phone milestone assumed an on-premises PBX on the same network.
It no longer applies: with a provider number the audio crosses the internet either
way, so the "same LAN, no NAT" property is not available to give up.

The two rejected options, and why:

- **Direct SIP** via `pjsua2` / `baresip` / `pyVoIP` — genuinely one script, but
  turn-taking and barge-in get written by hand and thrown away immediately after.
  Those two are the hardest part of the whole product; hand-rolling them to save
  a dependency is the wrong trade.
- **LiveKit self-hosted** — correct eventually, and required for the on-premises
  story. It costs two services running before the first call is answered, which is
  the delay this decision exists to avoid. It follows once calls are answered.

**This is a first-call decision, not the product's architecture.** The shipped
product must still support a self-hosted media path — an installation whose audio
is forced through a vendor's cloud contradicts the reason this project exists.
Anything written now must sit behind the interfaces in §B3, so that swapping the
transport later is configuration and not a rewrite.

---

## Never name a competitor in public

**No competing product or company is named anywhere public** — not in the repository,
a commit message, an issue, a pull request, the README, the specification, the website,
a release note, or a social post. In any language, in any spelling, not once.

Describe what Tel-Agent *is*. Never describe who it is against.

**This is not reversible.** A name that reaches a public repository stays in the git
history, in every fork, and in anything that mirrored it. Deleting the file afterwards
does not remove it. So the check happens **before** the commit, not after.

Competitive notes belong in , which is gitignored and never published.

---

## Code conventions

**Python**
- Python 3.11+ (3.12 on the current dev machine)
- Type hints on every public function
- `async`/`await` throughout the audio path — no blocking calls on the media thread
- Formatting: `ruff format`; linting: `ruff check`
- Configuration comes from environment variables only. Never assume Docker.

**Secrets** — two kinds, two homes (full reasoning in §B9.2)
- **`.env`** holds *installation* secrets: `DATABASE_URL`, `REDIS_URL`,
  `ENCRYPTION_KEY`, and the LiveKit keys. One set per installation, set once by
  whoever runs the server
- **The database, encrypted**, holds *user-entered* credentials: provider API keys,
  per-number SIP credentials, and channel tokens. Entered from the UI, changeable
  without a restart, and there can be many of each
- `ENCRYPTION_KEY` in `.env` is what encrypts those columns. It must never sit in the
  same place as the data it protects
- **`.env` is not the safer option for user credentials.** It is plaintext on disk, so
  one file read exposes everything; encrypted columns force an attacker to obtain both
  the database and the key. Database dumps, backups and read replicas are where
  credentials actually leak
- Milestone 0 is the exception — no database exists yet, so everything is in `.env`.
  This ends at Milestone 1. Never build a settings screen that writes to `.env`
- Never log a key, never commit one, never return one in full to a client — the UI
  shows a masked preview with the last four characters
- `.env.example` documents every variable with a safe placeholder

**Call data**
- Recordings and transcripts are personal data under GDPR
- They stay on the machine that produced them and are gitignored
- The recording announcement defaults to on — Austria requires both parties to
  be aware, and the requirement still applies once a human joins the call

**Data model** — six decisions that are painful to add later, so they are made now:
1. `user_id` on every table from day one, even while it is always `1`
2. A full-text index on `messages.text` in the first migration
3. `numbers.owner` — customer or platform holds the number. Separates a self-hoster's
   own number from one resold by Tel-Agent Cloud, and governs who may release or port it
4. `calls.billable_seconds` and `calls.provider_cost_micros` — usage metering from the
   first stored call. Integer micros, never floats
5. `messages.stt_confidence` and `.language` — per line. Turns "German accuracy" from
   an impression into a query. Null on text channels, which is itself the signal that
   a line was typed rather than spoken
6. **`conversations` is the core table, not `calls`.** A phone call is a conversation
   on a `phone` channel, plus a `calls` row for what only a call has — caller number,
   recording, billable seconds, provider cost. `channels` exists from the first
   migration holding one row of kind `phone`. Same discipline as `user_id` being
   permanently `1`: the structure is what makes Milestone 11 a write, not a redesign

**Git**
- Commit messages in English, imperative mood
- The CLA must be in place before the first external PR is merged; after that
  it becomes practically impossible to obtain retroactively

---

## Where things are

| Path | Contents |
|---|---|
| `docs/SPEC.md` | The complete build specification — single source of truth |
| `docs/DESIGN_BRIEF.md` | Design starting point; the call detail screen comes first |
| `IDEAS.md` | Parking lot for everything not in v1 |
| `CLA.md` | Contributor License Agreement |
| `.env.example` | Every environment variable, documented |
| `AGENTS.md` | Short entry for any coding agent; this file remains the full working contract |
| `GEMINI.md` | Pointer to `AGENTS.md` for Antigravity and Gemini CLI. Holds no rules of its own |
| `.github/copilot-instructions.md` | Pointer to `AGENTS.md` for Copilot. Holds no rules of its own |
| `.cursor/rules/` | Cursor-scoped rules that refine `AGENTS.md` for `web/`, Python paths, and `locales/` |
| `.claude/skills/contributing/` | The contributor workflow as a skill — clone to fork to merged pull request. It ships with the repository, so an agent working inside a clone already has it. |
| `internal/` | **Gitignored here, and does not exist in this repository.** The working notes live in a private repository instead; the ignore entry is kept as a guard so the folder cannot reappear here by accident. Never put a credential anywhere, in either repository. |

When `docs/SPEC.md` and this file disagree, the specification wins — and this
file should be corrected.
