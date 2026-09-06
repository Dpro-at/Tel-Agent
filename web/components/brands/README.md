# Vendored brand marks

Fifteen logos, copied into this repository on 2026-08-24, and eight AI-provider marks on 2026-09-06 (same source, same conversion) from
[theSVG](https://thesvg.org) (`https://thesvg.org/api/registry.json`).

`marks.tsx` is generated from the files in `source/`. Regenerating it means fetching the
variant named below again and re-running the same conversion: strip `<title>`, keep the
`viewBox`, camel-case the SVG attributes for JSX, and — for the two single-colour marks
noted below — swap the hard-coded fill for `currentColor`.

| Slug | Product | Variant taken | Licence | Kind here |
|---|---|---|---|---|
| `whatsapp` | WhatsApp | mono | CC0-1.0 | mono |
| `telegram` | Telegram | mono | CC0-1.0 | mono |
| `messenger` | Messenger | mono | CC0-1.0 | mono |
| `instagram` | Instagram | mono | CC0-1.0 | mono |
| `discord` | Discord | mono | CC0-1.0 | mono |
| `signal` | Signal | mono | CC0-1.0 | mono |
| `matrix` | Matrix | mono | CC0-1.0 | mono |
| `anthropic` | Anthropic | mono | CC0-1.0 | mono |
| `mistral` | Mistral | mono | MIT | mono |
| `openai` | OpenAI | default | MIT | mono, `#fff` → `currentColor` |
| `twilio` | Twilio | default | MIT | mono, `#e31e26` → `currentColor` |
| `ntfy` | ntfy | mono | CC0-1.0 | mono |
| `deutsche-telekom` | Deutsche Telekom | mono | CC0-1.0 | mono |
| `slack` | Slack | default | MIT | art, unmodified |
| `groq` | Groq | default | MIT | bleed, unmodified |
| `google-gemini` | Gemini | mono | CC0-1.0 | mono |
| `kimi` | Kimi | default | MIT | bleed, unmodified |
| `deepseek` | DeepSeek | default | MIT | mono, `#4D6BFE` → `currentColor` |
| `grok-xai` | Grok (xAI) | mono | MIT | mono |
| `qwen` | Qwen | default | MIT | mono, `#ffff` → `currentColor` |
| `openrouter` | OpenRouter | mono | CC0-1.0 | mono |
| `togetherdotai` | Together AI | mono | MIT | mono |
| `perplexity` | Perplexity | mono | CC0-1.0 | mono |

## Why the files are here and not on a CDN

Tel-Agent runs on the customer's own machine. An icon loaded from
`https://cdn…` breaks on an installation with no internet access and leaks one request
per view to a third party. Everything the interface draws ships with the interface.

## The licence on the code is not the licence on the mark

theSVG's own tooling is MIT; each icon carries its own `license` field, and the table
above records the one that came with each file. All fifteen are CC0-1.0 or MIT, which is
why these were taken and others were not — `Gong`, for one, is marked
`Trademark` and `azure` is marked `brand-use`, and both need the owner's brand
guidelines read before they appear in a shipped interface.

Two marks were recoloured rather than reproduced exactly. Both are single-colour logos
where the published file hard-codes a fill that would be invisible in one of our two
themes, and `currentColor` is what lets the same file work on both grounds. The shape is
untouched. Where artwork is genuinely multi-colour — Slack, Groq — it is kept exactly as
published, because recolouring those would make them different logos.

## A mark with no colour of its own

Four of these are black or near-black by definition — Matrix, OpenAI, Anthropic, Twilio —
and one registry field says so: their `hex` is `#000000` or close to it. Drawing them "in
the brand's own colour" paints them invisible on the dark canvas, which is exactly what
happened the first time. `brand-mark.tsx` measures the luminance and treats anything too
dark or too light as neutral: it draws in `--od-text-3`, so the mark follows the theme and
stays legible on both grounds. A mark that does have a colour — WhatsApp green, Instagram
pink, Mistral orange — keeps it, and tints its container with it.

## What is deliberately not here

The catalogue names 64 products; theSVG has 23 of them. The rest are mostly German and
Austrian vertical software and phone systems — Doctolib, casavi, STARFACE, Tomedo,
onOffice — and no registry carries them. Those keep their lettered marks.

Where the marks are used is decided per set, in `BRANDED_CATEGORIES` in `apps.tsx` and by
`brandSlug` elsewhere:

- **The channels** — the installer's channel list and the store's channels category. Every
  company in them has a mark; the four that are not companies (a phone line, web chat, SMS,
  email) carry a drawn glyph, which is what they should have had all along.
- **The model providers** — the four in the installer. "Any OpenAI-compatible URL" keeps
  its neutral mark, because it is a description and not a company.
- **Notifications** — Slack and ntfy, both covered.
- **SIP** — knowingly incomplete. Twilio and Telekom CompanyFlex have marks; sipgate,
  toplink, ecotel and A1 Telekom Austria have none in any registry, so a logo sits beside a
  letter here. It was judged worth it: those four would have to be drawn by hand, and a
  recognisable Twilio is worth more than a uniform row of letters.

Everything else in the store keeps letters, including companies whose marks exist —
Stripe, Odoo, DATEV, Grafana, Google Calendar. Vendoring those is a separate decision per
category, not a default.
