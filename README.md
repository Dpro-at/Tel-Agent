<div align="center">

<img src="logo.png" alt="Tel-Agent" height="130">

# AI phone assistant

**Connect any phone line to any AI model. Self-hosted, bring your own keys.**

<!-- Eleven marks, ten channels: call and landline are two drawings of the one phone
     channel. This row is about recognition, not arithmetic. -->
<p>
  <img src="docs/brand/channels/call.svg" alt="Call" title="Call" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/landline.svg" alt="Landline" title="Landline" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/web-chat.svg" alt="Web chat" title="Web chat" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/sms.svg" alt="SMS" title="SMS" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/email.svg" alt="Email" title="Email" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/whatsapp.svg" alt="WhatsApp" title="WhatsApp" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/telegram.svg" alt="Telegram" title="Telegram" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/messenger.svg" alt="Messenger" title="Messenger" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/instagram.svg" alt="Instagram" title="Instagram" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/discord.svg" alt="Discord" title="Discord" height="26">
  &nbsp;&nbsp;
  <img src="docs/brand/channels/slack.png" alt="Slack" title="Slack" height="26">
</p>

<p>Connect them over MCP — and get full control.</p>

<!-- Thirteen logos, one file per theme. Seven of them are single-colour marks, so the
     strip is built twice rather than recoloured live. See docs/brand/models/README.md. -->
<p>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/models/models-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/brand/models/models-light.svg">
  <img src="docs/brand/models/models-light.svg" alt="Works with OpenAI, Claude, Gemini, Mistral, Ollama, Perplexity, Copilot, Manus, DeepSeek, Grok, Qwen, Groq and OpenRouter" height="28">
</picture>
</p>

<!-- Two files, one seal. The wreath is single-colour artwork on a transparent
     background, so it needs dark ink on GitHub's light theme and white ink on the
     dark one; <picture> is what lets a README ship both. -->
<p>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/awards/product-of-the-day-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/brand/awards/product-of-the-day-light.svg">
  <img src="docs/brand/awards/product-of-the-day-light.svg" alt="Product of the day - 1st" height="74">
</picture>
</p>

<p>
  <img src="docs/brand/compliance/gdpr-dsgvo.png" alt="DSGVO & GDPR compliant" height="34">
  <img src="docs/brand/compliance/eu-ai-act.png" alt="EU AI Act compliant" height="34">
</p>

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#project-status)

[tel-agent.com](https://tel-agent.com) · maintained by [Dpro GmbH](https://dpro.at), Vienna

</div>

---

## What it is

Tel-Agent is an open-source gateway that sits between a phone line and an AI agent.

A call arrives over SIP. Tel-Agent checks the caller against your routing rules and
either passes it through to a human, blocks it, or hands it to an AI agent. The agent
speaks with the caller in real time, can invoke tools — transfer the call, take a
message, check a calendar, call any HTTP endpoint — and every call is recorded,
transcribed, and searchable.

It runs on your own hardware, on your own LAN, with your own API keys.

The phone comes first and is the hardest case. After it works, the same agent answers
on **web chat, SMS, email, WhatsApp, Telegram, Messenger, Instagram, Discord and
Slack** — connected with your own credentials from each platform, never a shared
application of ours. **Ten channels, and the list is closed.** A channel is a route a
customer uses to reach you; a system you run your own business on is an integration,
and those are reached through webhooks and the HTTP tool.

## What it is not

- **Not a PBX replacement.** It connects to your existing PBX (3CX, Asterisk, FreePBX)
  as an extension.
- **Not a workflow automation platform.** Webhooks and a generic HTTP tool reach n8n
  and Home Assistant, which do that job better. A *channel* is where the conversation
  happens; an *integration* is a system the agent acts on. We own the first and reach
  the second through the HTTP tool.
- **Not a CRM.**
- **Not analog-capable.** Bridge an analog line with an ATA; Tel-Agent only speaks SIP.

## Why it exists

Well-funded closed products already do "AI receptionist for business". What does not
exist is a good **open, self-hosted** one — where you own the recordings, choose the
models, and decide which callers ever reach the AI at all.

---

## Project status

**Pre-alpha. Not usable yet.** There is no installable release.

The project is at Milestone 0: getting a single conversation answered end to end in a
web chat — the reply streaming token by token, interruptible mid-sentence, with the
message captured and the transcript printed. The messaging channels follow, and **the
phone comes last**, at Milestone 11.

**Most of the screens now have something behind them.** Twenty-three are served by a
real API — sign-in and the account flows, home, the conversation archive and one
conversation in full, the notification tray, contacts, assistants, knowledge, the
catalogue, apps, numbers, routing rules, backups, system health, settings and
workspaces. Eight are still design with fixture data in them: the calendar, outbound
campaigns, connectors, the consent log, the live call, usage, updates and the install
wizard. Every one of those eight belongs to a milestone that has not been reached.

**This is still not a product you can run.** There is no installable release, no
packaging, and the agent does not answer with a model until one is configured — the
loop that carries the reply is built and the model is a key away.

The build order is deliberate, and it was reversed once, on 2026-08-22. It originally
required an answered phone call before anything else was built; the phone loop was
proven elsewhere, which retired the risk that ordering existed to cover. What was not
proven is that anyone can reach the agent at all. The superseded rule and the cost of
reversing it are recorded in `internal/DECISIONS.md` as D-017.

**Milestone 0 of 12 — in progress.** The full plan, and what each milestone means, is
in [`docs/ROADMAP.md`](docs/ROADMAP.md).

Watch or star the repository if you want to know when it becomes installable.

---

## Planned architecture

| Layer | Choice |
|---|---|
| Voice agent | Python + [LiveKit Agents](https://github.com/livekit/agents) |
| API | Python + FastAPI |
| Frontend | Next.js + React |
| Database | PostgreSQL — transcripts need real full-text search |
| Cache / queue | Redis |
| Reverse proxy | Caddy — automatic HTTPS |
| Packaging | Docker Compose |

**Providers for v1:** Deepgram (STT) · one cloud LLM · ElevenLabs (TTS).
Local models (Ollama, Whisper, Piper) follow in v1.1 — they need a GPU to hold a
natural conversation, so they are not the default.

**Latency target:** under 800 ms from the end of caller speech to the first audio out.
Everything streams; the first sentence starts speaking while the rest is still being
generated.

---

## Quick start

```bash
git clone https://github.com/Dpro-at/Tel-Agent.git
cd Tel-Agent
cp .env.example .env
# set ENCRYPTION_KEY in .env - generate one with: openssl rand -hex 32
docker compose up -d --build
```

Then open **http://localhost:3000**. The first visit creates the administrator —
there are no default credentials. The API and its documentation are on
http://localhost:8000/docs, and conversations live on the `tel-agent-data`
volume (SQLite by default; a `postgres` profile is in `docker-compose.yml`).

From the first tagged release on, the images are also published to GitHub
Container Registry, so the build step can be skipped entirely:
`docker compose -f docker-compose.release.yml up -d` — same layout, same
volumes, interchangeable with the from-source file on one machine.

Both ports are published on **loopback only**. Reaching the installation from
other machines is a decision made in `.env` — the `TEL_AGENT_*` block there
lists the three values to change and why the dashboard image is rebuilt for it.
On a server, put a reverse proxy terminating TLS in front instead.

**Running it without Docker** stays supported and documented — contributors need
to run the code without rebuilding an image on every edit. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the manual run, [`docs/SPEC.md`](docs/SPEC.md)
for the full design and [`CLAUDE.md`](CLAUDE.md) for the development rules.

### Requirements when it ships

- API keys for an STT, an LLM, and a TTS provider (or a GPU for local models)
- A machine on the same LAN as the PBX
- Network access to the PBX on 5060/UDP and an open RTP port range
- A SIP endpoint. Most lines already are one:

| Your line | What you need | Extra hardware |
|---|---|---|
| A PBX — 3CX, Asterisk, FreePBX | An extension on it | None |
| A landline from an ISP | These are IP-based now. Either the provider gives you SIP credentials, or your router acts as a SIP registrar and you register against it — common with Fritz!Box in Austria and Germany | None |
| A genuinely analog line — old copper, a fax line | An ATA to bridge it, e.g. a Grandstream HT801 | ~€30 |

Tel-Agent only ever speaks SIP. That is deliberate: supporting telephony hardware
directly is a project of its own, and an ATA solves it for about the price of a
cable.

---

## Security notes

- **The port is not exposed by default.** The server listens on `127.0.0.1`, and
  `python -m api` warns if you change that. The supported ways to reach an installation
  from elsewhere — a private network, a VPN, or a reverse proxy terminating TLS — all
  talk to a server on loopback, so none of them requires widening it.
- **A password is required on first run.** There are no default credentials.
- **API keys are encrypted at rest** and are never returned in full to the client.
- **Recording announcements are on by default.** Austria requires both parties to be
  aware that a call is recorded, and the requirement still applies once a human takes
  over from the agent.

---

## Contributing

**New here? [`docs/ONBOARDING.md`](docs/ONBOARDING.md) is the hour-long path from a fork
to a merged pull request** — a translation, one file, no setup and no API keys. Thirty
languages are sitting at 0% and any language you actually speak is welcome.

Contributions are welcome, but note the current state: until the agent answers on one
channel end to end, pull requests adding features will be pointed at
[`IDEAS.md`](IDEAS.md) rather than merged. That is not a judgement on the idea — it is how this project stays finishable.

**All contributors must sign the [CLA](CLA.md)** before their first pull request is
merged. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening one. Everything in the
repository — code, comments, commit messages, documentation — is written in English.

<!-- Two marks, inked per theme. These are the agents the repo is configured for, not
     models Tel-Agent connects to — that row is at the top. docs/brand/agents/README.md -->
<p>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/brand/agents/agents-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/brand/agents/agents-light.svg">
  <img src="docs/brand/agents/agents-light.svg" alt="Built with Antigravity and Codex" height="24">
</picture>
</p>

**Working with an AI coding agent?** Point it at [`AGENTS.md`](AGENTS.md) — Codex,
Antigravity and the rest find it on their own. [`CLAUDE.md`](CLAUDE.md) is the full
working contract; every other entry file in the repository is a pointer to those two, so
the rules cannot drift apart.

Found a security problem? **Do not open a public issue** — see
[`SECURITY.md`](SECURITY.md).

---

## License

[AGPL-3.0](LICENSE). Copyright © Dpro GmbH.

If you run a modified version of Tel-Agent as a network service, you must publish your
modifications. The free version is never crippled; it is the product.

For a commercial license permitting closed-source integration, contact
Dpro GmbH at [info@dpro.at](mailto:info@dpro.at).

Dpro GmbH · Wipplingerstraße 20/18, 1010 Wien, Austria · FN 631492s, Handelsgericht Wien
