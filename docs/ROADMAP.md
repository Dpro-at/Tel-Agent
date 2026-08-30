# Roadmap

Twelve milestones. **Step N+1 does not start before step N works.**

This is the public, milestone-level view. Day-to-day tasks live in
[GitHub Issues](https://github.com/Dpro-at/Tel-Agent/issues); ideas that are not in v1
live in [`IDEAS.md`](../IDEAS.md).

---

## Where the project is now

**Milestone 0. Nothing else is being worked on.**

That is not a formality. The previous attempt at this stalled with plenty of plan and
nothing a customer could reach. The order is the only thing being changed this time.

> **Nothing gets built until the agent answers on one channel, end to end.**
> Not the second channel. Not the rules engine. Not the UI beyond what it takes to
> watch one conversation happen.

Milestone 0 has a **two-week time box**. If it is not working by then, the constraint
is time rather than architecture — and learning that early is worth more than any
feature.

---

## Why the phone is last, and what that costs

This roadmap used to open with the phone. It was ordered that way because the phone is
the hard case — no interface, no way to show the caller what was understood, a
sub-second latency budget, and a caller who interrupts mid-sentence — and because an
architecture built to satisfy text channels can look healthy while being far too slow
for voice.

That order was reversed on 2026-08-22 by decision **D-017**; the superseded text and
the cost of reversing it are kept in `internal/DECISIONS.md`, the same way CLAUDE.md
records it.

The reasoning was sound, and one thing changed it: **the phone loop is already
proven.** A number rings, a script answers, speaks, listens and replies — demonstrated
outside this project. The risk the old order existed to retire is retired. What is not
proven is that anyone can reach this agent at all, and that is what the first
milestones buy.

The cost is real, and is written here so nobody rediscovers it as a surprise:

- **The hardest measurement now comes last.** A loop that is comfortable in a chat
  window can be hopeless on a call. Latency, endpointing and barge-in are not measured
  until Milestone 11.
- **The mitigation is a budget written down now, not later.** Every interface built
  before Milestone 11 is shaped for streaming: partial results in, output in chunks,
  and a `cancel()` that stops generation immediately. An interface that returns one
  finished answer is the shape that cannot be fixed later without rewriting everything
  above it.
- **Text-channel comfort is not evidence.** No milestone before 11 may claim the
  latency question is answered.

---

## 0 — Web chat · **in progress**

A visitor types on a web page, the agent answers, the thread holds, and a transcript is
printed. One process, one page, one model.

**Revised 2026-08-26 by D-027.** This milestone was written as "the page and a script" —
no database, no dashboard, no login. That is no longer what is built first. The
foundations below come before it, because twenty-nine screens already exist and none of
them can be reached by a real user without them.

Milestone 0 keeps its six checks and its meaning: **the conversation loop is the
product.** What changed is only what is standing underneath it when it arrives.

**Foundations, before the six checks:** the tooling gate that makes the working rules
enforceable, the backend skeleton, the first migration, the authentication model
(D-030), workspaces (D-028) and the extension contract (D-031). Web chat is then built
as the **first official application on that contract** — which is the mitigation D-027
records, and the reason the contract does not go unproven.

**No routing rules. No provider abstraction beyond the model.** Those still wait.

Six checks, in order:

| # | Check | Done when |
|---|---|---|
| 0 | **Refuse** | A message from an origin the channel does not allow is refused, and nothing is stored |
| 1 | Arrive | A message typed in a browser reaches the agent process |
| 2 | Answer | It replies with a hardcoded greeting — **and streams it**, because an interface that returns one finished answer is the one Rule 3 says cannot be fixed later |
| 3 | Understand | The model's reply appears in the page, in the visitor's language |
| 4 | **Full loop** | Visitor writes → model replies → visitor writes again, thread intact |
| 5 | **Stream and cancel** | Tokens appear as they are produced, and stop instantly when cancelled |
| 6 | Take a message | It asks for name and reason, prints a structured result |

**Step 0 comes before step 1 for a reason.** This endpoint is the only public,
unauthenticated one in the product (§B14): anybody who reads the customer's page has its
address. Built in that order the guard is a condition of the first message arriving;
built afterwards it is a change to something already working, which is the change that
gets postponed. The order is the whole protection.

Steps 1–2 are plumbing. **Step 4 is the product. Step 5 is what protects Milestone 11.**

**Where this stands.** Steps 0, 1 and 2 are done and proven in a browser, not only in
tests: a message from an origin the channel does not allow is refused and nothing is
stored; a message typed into the embedded widget reaches the agent process, is stored,
and raises a notification so a person knows a stranger wrote in; and the greeting
arrives streamed, chunk by chunk, over server-sent events that close when the reply
ends.

Step 3 is built and not yet proven. The model sits behind `LLMProvider` (§B3) with one
implementation - an OpenAI-compatible `/chat/completions` endpoint, which is the shape a
hosted gateway and a model on your own machine both speak - and `agent.reply` streams
from it, asking for the answer in the language the visitor wrote in. What it has not had
is a key: the check says *the model's reply appears in the page*, and that is a browser
away from a configured installation, not a test away. Until one is configured the agent
says so in words and the message is still stored, which is the honest state of a fresh
install rather than a placeholder.

Step 5 is not a nicety. It is the whole of what the old phone-first order was
protecting: an agent that composes a complete answer and then sends it is an agent that
can never be put on a phone. Cancellation is proven here, in the easy case, while it is
still cheap to get right.

The model sits behind one interface from the first commit — changing the model must
never mean editing the page. Speech-to-text and text-to-speech get the same treatment
when they arrive, at Milestone 11.

**Measured:** time to first token, whether the thread survives a page reload, and
whether cancel actually stops generation rather than only hiding it.

## 1 — Persistence

PostgreSQL. Conversations and messages stored.

Six things that are painful to add later and are therefore done here, in full in
`docs/SPEC.md` §B5. The two that shape everything else:

**`conversations` is the core table, and there is no `calls` table yet.** A phone call
will be a conversation on a channel of kind `phone`, plus a `calls` row for what only a
call has — the caller's number, the recording, the billable seconds, the provider cost.
Everything built for chat then works on the phone without a branch. The phone-first
order reached this same decision from the other end, which is the sign that reordering
the milestones is a change of sequence and not of architecture.

**`user_id` on every table even while it is always `1`**, and a full-text index on
`messages.text` in the first migration.

## 2 — Web UI

In this order: **conversation detail** → conversations list → home → rules → agent →
settings.

Conversation detail is designed and built first, alone. It is the heart of the product
and it settles the vocabulary every other screen inherits. Onboarding is designed last,
because it should be assembled from components the rest of the product already proved.

No screen may assume a channel. A conversation is a conversation; the phone is a kind,
not a layout.

## 3 — Messaging channels

WhatsApp, Telegram, Messenger, Instagram, Discord, Slack — plus SMS and email. The same agent,
the same tools, the same searchable archive; a different transport.
**Ten channels including the phone, and the list is closed.**

The line that keeps it closed: a channel is any route a **customer** uses to reach a
business. It is not any system the business itself runs on — Teams and project
trackers are integrations, reached through the HTTP tool. Slack is on the list for the
shared-channel case only: an outside customer in a channel with a supplier is a route
in, an internal workspace is not. Without that line, "add one more connector" has no
end.

WhatsApp and Telegram come first: that is where the customers of the businesses this is
built for already are, and Telegram needs no platform review at all. Email follows. SMS
arrives with the telephony account, which does not exist until Milestone 11 — it is the
one channel here that may legitimately land late.

The customer connects credentials from their own developer account on each platform.
Tel-Agent never holds a shared platform application: one shared app would put every
installation behind a single rate limit, and make one policy violation everybody's
outage.

Setup requirements per channel are specified in `docs/SPEC.md` §B13.

## 4 — Routing rules

Pass through, block, or hand to the AI, decided from who is making contact and when.
Business hours.

Rules are written against a channel identity rather than a phone number: a WhatsApp
number, a Telegram handle, an email address — and later a caller ID — all resolve to
the same contact. Building it any other way means writing the rules engine twice.

**Phone routing is not finished in this milestone.** It rests on a question only a live
line can answer: whether a forwarded call carries the original caller's number or the
forwarding subscriber's. Some carriers present the latter, which would leave every rule
matching the same number on every call. That is settled at Milestone 11, and where the
original survives in a diversion header it is read from there.

## 5 — Tools

Transfer, take a message, end conversation, notify, generic HTTP request, search
knowledge, check calendar.

Kept short on purpose: five precise tools beat twenty that confuse the model. The
calendar tool proposes and confirms, or writes to a review calendar — it does not book
directly into a live calendar in v1. One wrong entry destroys trust permanently.

## 6 — Webhooks and REST

The documented, signed public API. The dashboard already consumes it, so it exists
anyway — this milestone makes it public and documented.

## 7 — MCP server

A thin layer over the REST API, with hard limits. An external model that can start real
conversations — and later real calls — spends real money.

## 8 — Live intervention

**Whisper first** — the operator types an instruction and the agent delivers it in its
own voice; the customer never knows. Highest value, lowest complexity, and on a text
channel it is nearly free.

Takeover follows. Note that browsers block microphone access over plain HTTP outside
`localhost`, so voice takeover needs HTTPS; until then, whisper and type-to-speak only.

## 9 — Health and alerts

Not a feature. A silently dead service is worse than an obviously dead one, because the
operator only finds out after losing ten conversations.

Real checks on every connected channel, on provider reachability and on the database;
immediate alerting on a channel that stops delivering; per-message latency telemetry.
SIP registration joins this list at Milestone 11.

## 10 — Docker packaging

One-command install. Manual development runs stay documented — contributors need to run
the code without rebuilding an image on every edit.

## 11 — Phone call

A phone number rings, the agent answers, speaks, listens, replies, takes a message, and
the transcript lands in the same archive as every other conversation.

The number comes from a SIP provider and is pointed at the agent. An extension on an
existing PBX reaches the same place and stays a first-class way to connect a line.

This milestone brings the two provider interfaces chat never needed — speech-to-text
and text-to-speech, each behind a clean interface. **`cancel()` on text-to-speech is
mandatory:** on interruption, audio stops instantly and queued speech is discarded. The
model-side cancellation it depends on was proven at Milestone 0.

Six checks, in order:

| # | Check | Done when |
|---|---|---|
| 1 | Arrive | The provider console shows the inbound call reaching our SIP endpoint |
| 2 | Answer | You call it, it stops ringing, the line goes quiet |
| 3 | Speak | It answers with a hardcoded greeting |
| 4 | Listen | Your words appear as text |
| 5 | **Full loop** | You speak → the model replies → you hear the reply |
| 6 | Same archive | The call sits beside the chats, searchable, with a transcript |

Before any code: buy the number, point it at the SIP endpoint, call it from a mobile,
and confirm in the provider console that the call arrives. If it does not, the problem
is in the number configuration, and debugging SIP through our own code is far harder.

**Measured:** time from end of speech to first audio out (target under 800 ms), where
that time goes — **endpointing included, as it is usually the largest stage** — what
happens when the caller interrupts, and accuracy across at least 20 real calls in
Austrian German including names and addresses. **If latency exceeds ~1.5 s, nothing
else is added until streaming is fixed.**

**Also recorded on the first forwarded call:** which number arrives in the caller ID
when a call is forwarded rather than dialled directly — the original caller's, or the
subscriber's. This is carrier-dependent, and it decides whether the phone half of
Milestone 4 works at all.

**This is last on purpose, and it is the milestone that judges the ten before it.**
Everything above was built to a latency budget it was never forced to meet. Here it is
forced.

---

## Not on this roadmap

| | Why |
|---|---|
| General workflow automation | Webhooks and a generic HTTP tool reach n8n and Home Assistant, which do it better |
| Integrations with SaaS applications | A **channel** is where the conversation happens; an **integration** is a system the agent acts on. We own the first and reach the second through the HTTP tool. Channels are a closed list of ten; integrations are unbounded, which is why they are somebody else's product |
| Being a CRM | Not what this is |
| Being a PBX replacement | It connects to your PBX as an extension |
| Analog hardware support | We only ever speak SIP. A genuinely analog line is bridged with an ATA — see the requirements in the README |

Everything discussed but not scheduled is in [`IDEAS.md`](../IDEAS.md). That file is
the mechanism that gets this project finished: ideas go there instead of into the code.
