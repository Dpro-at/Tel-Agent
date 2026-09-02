# IDEAS.md — the parking lot

Everything discussed but not in v1 goes here.

This file matters more than it looks. Every good idea that arrives mid-build gets
written down here **instead of into the code**. It is the mechanism that gets this
project finished. The ideas will still be here when they are needed, and they will
not distract now.

**The one rule:** nothing gets built until the agent answers on one channel, end to end.
Not the UI. Not the rules engine. Not the second provider. Not MCP.

---

## How to add an entry

Append to the right section with a one-line description and, where it matters, the
reason it is deferred. No estimates, no priorities — this is a list, not a roadmap.
Items graduate out of this file only when an earlier milestone is finished.

---

## Deferred from the specification

These are already described in `docs/SPEC.md` and scheduled, just not now.

- **Provider abstraction** (§B3) — Milestone 1. One implementation each of STT / LLM /
  TTS first; the second implementation comes only after one channel works end to end.
- **Additional providers** (§B3) — Ollama, local Whisper, Piper, Cartesia. Many will
  arrive as community pull requests.
- **PostgreSQL persistence** (§B5) — Milestone 2.
- **Routing rules engine** (§A6.5, §B11) — Milestone 3.
- **Web dashboard** (§A6) — Milestone 4, starting with the call detail screen.
- **Webhooks and public REST API** (§B6) — Milestone 5.
- **Built-in tools** (§B7) — Milestone 6.
- **Live intervention: whisper, then takeover** (§A6.7) — Milestone 7. Whisper first:
  highest value, lowest complexity.
- **Health checks and alerting** (§B8) — Milestone 8.
- **Docker packaging** (§B10) — Milestone 9.
- **MCP server** (§B11) — Milestone 10. A thin layer over the REST API, with hard
  limits: an external model that can start real calls spends real money.
- **Buying phone numbers in-app** (§A6.1) — v1 connects a number the user already
  bought, or an existing SIP extension. Provisioning from inside the UI comes later.
- **`NumberProvider` interface** — *promoted out of this file on 2026-08-17.* Now
  specified in §B3.1, because Milestone 0 depends on it: the first call arrives on a
  provider number rather than a PBX extension. The interface itself is still written
  at Milestone 1 with the other provider interfaces; what changed is that the shape
  and the ordering of its implementations are now decided rather than deferred.
- **Knowledge sources and embeddings** (§A6.6, §B5) — `search_knowledge` is expected
  to become the most used tool, but not before the call works.
- **Contacts and per-caller history** (§A6.9).
- **Arabic RTL layout** (§A4) — the locale files exist from day one; the mirrored
  layout work lands with the UI.

---

## Distribution

- **n8n community node for Tel-Agent** — one of the strongest distribution channels
  available; a large community actively looks for new nodes.
- **A 30-second video of a real call** — carries more weight than any README section.
- **Launches on Hacker News and r/selfhosted** — an excellent project nobody finds is
  a dead project.
- **Importable n8n workflow examples** under `examples/workflows/`.

---

## Open questions

- **Trademark position on "Tel-Agent"** (EUIPO classes 9 and 42). An older academic
  dialogue-systems framework shares the name. Confirm before committing to a logo.
  *Not legal advice.*
- ~~**How SIP is handled in Milestone 0**~~ — **answered 2026-08-17: LiveKit Cloud
  SIP.** Once the first call arrives on a provider number rather than an on-premises
  PBX, the audio crosses the internet regardless, and the "same LAN, no NAT" objection
  to LiveKit Cloud no longer applies. See `CLAUDE.md`. The self-hosted media path is
  not abandoned — it returns at Milestone 9.
- **Does a forwarded call carry the original caller's number?** Carrier-dependent, and
  Milestone 3's routing rules are worthless if the answer is no. Measured on the first
  forwarded call in Milestone 0 rather than discussed here.

---

## Ideas raised during the build

*(Add new entries below. Date them.)*

**2026-08-17 — Selling numbers to customers (Tel-Agent Cloud only).**
Dpro GmbH holds numbers at Twilio and assigns them to customers, who then forward
their existing line to the assigned number on no-answer after a set number of seconds.
Onboarding collapses to one button instead of a provider account, a document upload
and a trunk configuration.

Deferred, and deliberately so:

- It is a compliance function before it is a feature — per-country regulatory bundles,
  in-country addresses for geographic numbers, emergency-service questions where the
  number fronts somebody's main line, and being holder of record when a customer ports
  away. Detail in §B3.1.
- The margin is negligible. A number is €1–3 per month against TTS at €0.06–0.10 per
  minute, so this is a conversion improvement, not a revenue line. Revenue comes from
  the subscription and the per-minute AI.
- It must never enter the open edition. What lands in the repository is a generic
  provider into which a user pastes their own credentials.

What was *not* deferred, because it is cheap now and expensive later: `numbers.owner`,
`numbers.provider_account_ref`, and per-call usage metering — all in §B5.

**2026-08-17 — Onboarding cost disclosure.**
Whoever forwards a call usually pays for the forwarded leg in the EU. A surprise on
the first invoice costs more trust than it saves in setup friction, so this belongs in
the onboarding copy, not in a support article.

**2026-08-22 — The voice orb as a product component.**
The landing page ends with a sphere that scales to the amplitude of whatever is
speaking, read from an `AnalyserNode`. The same component would work inside the
product: a live call view where the orb moves as the caller and the agent speak,
and a browser-based web chat where the visitor can talk instead of type.

Worth writing down because of one property that is easy to lose: it is a conic
gradient, three shadows and about twenty lines of JavaScript. **No Three.js, no
shader pack, no npm package, nothing to license** — so unlike the animation library
used on the landing page, it can go straight into `web/` without an AGPL question.

Deferred because Milestone 0 is the only milestone. Revisit at Milestone 6 (the call
view) or Milestone 11 (web chat), whichever arrives first. The reference
implementation and five rejected alternatives are in
`internal/brand-explorations/scroll-test/orb-lab/`.

**2026-09-02 — A relay service for self-hosted installations.**
A self-hosted installation sits behind a router on a private network, and that is a
problem twice over: the owner cannot reach their own dashboard from outside, and —
more importantly — **the messaging channels cannot work at all**. WhatsApp, Messenger,
Instagram and Telegram deliver messages by calling a public HTTPS webhook; a server on
a LAN has no such address, so the setup story becomes "get a public IP, open ports,
obtain a domain, configure TLS" — which ends most non-technical installations before
they begin.

The relay collapses that to nothing. Tel-Agent runs relay servers in a few regions;
the installation opens an **outbound** tunnel to the nearest one (no port forwarding,
no static IP at the customer's end) and receives a stable public address such as
`name.tel-agent.com`. That one URL then serves as:

1. the **webhook endpoint** the messaging platforms deliver to,
2. the **public chat page and embed widget** address,
3. **remote access** to the dashboard and the future desktop/mobile apps.

Properties that are part of the idea, not optional extras:

- **The relay stores nothing.** It forwards traffic; conversations, recordings and
  credentials stay on the customer's machine. TLS terminates at the installation
  (passthrough), so the relay cannot read what it carries. That is the promise that
  makes self-hosting meaningful, and it must survive implementation.
- **Voice never crosses the relay.** Call media takes its own path (§CLAUDE.md, SIP
  section). The relay carries text, webhooks and dashboard traffic only — this keeps
  both latency and bandwidth honest.
- **Custom domains.** A customer points `agent.their-company.com` at the relay via
  CNAME; certificates are issued automatically. Same mechanics as any hosted platform
  with custom domains.
- **Region-aware routing** — an installation connects to the nearest relay, chosen
  automatically.
- **Optional, always.** A technical user with their own public endpoint never needs
  it; the direct path stays first-class.

How the choice is presented — clarified 2026-09-02, because it decides whether the
open edition stays trustworthy:

- **The relay client ships inside the open edition**, not as a separate download.
  The settings screen offers one choice with three answers: local network only
  (the default), *my own public address* (a field for the user's domain or reverse
  proxy), or *connect to the relay* (one button, an account, a stable address).
- **Off by default, always.** The installation never contacts our servers until the
  owner explicitly turns the relay on. A self-hosted program that phones home
  unasked forfeits exactly the trust self-hosting exists to provide.
- **The own-address path is first-class**, on the same screen, never buried. Making
  our path easy must never make the user's own path harder.
- **The relay server itself is a hosted service run by Tel-Agent Cloud** and, like
  number reselling above, never enters the open edition. What lands in this
  repository is the client half: the tunnel the installation opens, pointed at
  whatever the user configures.

Deferred because Milestone 0 is the only milestone, and the relay matters at
Milestone 3 (messaging channels) at the earliest. The commercial side is recorded
in `internal/DECISIONS.md` (D-033).

**2026-08-20 — Redraw the logo as clean vector, and draw a 16 px icon.**
Everything in `docs/brand/tel-agent-logo/` is traced from raster artwork, not drawn.
It renders correctly and scales, but the full-colour marks are ~200 colour-band
paths each, so they cannot be recoloured or edited.

Single-colour treatments now exist — knockout, outline and silhouette, each in
purple, white and black — and the knockout mark is readable down to about 32 px.
Two things are still missing, and both are deferred:

- A hand-drawn vector version with real editable paths, so a colour is one value.
- A purpose-drawn 16 px icon. Every current treatment is a smudge at that size,
  and 16 px is what the browser tab actually shows.

Deferred because Milestone 0 is a two-week time box and no call has been answered
yet. A logo blocks nothing. Revisit once the phone rings and the agent replies.
