# AGENTS.md — guidance for AI coding agents

You are working in **Tel-Agent**, an AGPL-3.0 self-hosted AI phone and messaging
assistant by Dpro GmbH (Vienna). This file is the short entry point for any coding
agent (Cursor, Codex, Copilot, and similar).

**The full working contract is [`CLAUDE.md`](CLAUDE.md).** Read it before changing
code. When `docs/SPEC.md` and any other document disagree, the specification wins.

For the human contributor path (fork → claim → branch → PR), follow
[`.claude/skills/contributing/SKILL.md`](.claude/skills/contributing/SKILL.md).

---

## Non-negotiable

1. **English only** in code, comments, commits, PRs, and issues. UI strings live in
   `locales/` (`en` / `de` / `ar`).
2. **Never name a competitor** anywhere public — irreversible once pushed.
3. **Never commit secrets** — keys go in `.env` (ignored) or encrypted DB columns later.
4. **Stay inside the issue.** Extra ideas go in `IDEAS.md` or a new issue, not this branch.
5. **Milestone 0 first:** web chat streams and cancels; conversation is stored. Messaging
   channels next. **Phone is last (Milestone 11).** Write the conversation layer to voice
   constraints from day one (streaming, cancel, latency).
6. **Everything streams.** Target &lt;800 ms end-of-speech → first audio. Never wait for a
   full LLM reply before starting TTS. `cancel()` on TTS is mandatory.

---

## Repository layout

| Path | Role |
|---|---|
| `agent/` | Soft real-time conversation / call path (Python). Never serves the dashboard. |
| `api/` | FastAPI REST + WebSocket. Never touches audio. |
| `web/` | Next.js dashboard. Talks **only** to `api/`. |
| `locales/` | UI strings. One translated file per PR for first contributions. |
| `docs/SPEC.md` | Single source of truth for product behaviour |
| `docs/ARCHITECTURE.md` | What may import / talk to what |
| `IDEAS.md` | Parking lot for anything outside current milestone |

`agent/` and `api/` do not call each other — the database (and Redis for live events)
is the boundary. See `docs/ARCHITECTURE.md`.

---

## Verify before you open a PR

Run the issue's **Verify** commands first, then the gate for what you touched:

```bash
# Python (when agent/ or api/ exist and are in scope)
ruff check . && ruff format --check . && mypy . && pytest

# web/
npm --prefix web run lint
npx --prefix web tsc --noEmit
npm --prefix web run build

# locales (translation PRs)
node scripts/check-locales.mjs --locale <code>
```

Hand-check: no secrets, no competitor names, English only, one concern per PR.

Commits: Conventional Commits, English, imperative (`feat(api): …`, `docs: …`).
Branches: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`.

---

## Cursor-specific rules

Scoped project rules live in [`.cursor/rules/`](.cursor/rules/). They refine this file
for `web/`, Python paths, and `locales/` — they do not replace `CLAUDE.md`.
