# AGENTS.md — guidance for AI coding agents

You are working in **Tel-Agent**, an AGPL-3.0 self-hosted AI phone and messaging
assistant by Dpro GmbH (Vienna). This file is the short entry point for any coding
agent — Claude Code, OpenAI Codex, GitHub Copilot, Cursor, Google Antigravity,
Gemini CLI, Windsurf, Zed, Aider, and anything else that reads `AGENTS.md`.

**The full working contract is [`CLAUDE.md`](CLAUDE.md).** Read it before changing
code. When `docs/SPEC.md` and any other document disagree, the specification wins.

For the human contributor path (fork → claim → branch → PR), follow
[`.claude/skills/contributing/SKILL.md`](.claude/skills/contributing/SKILL.md).

---

## Non-negotiable

1. **English only** in code, comments, commits, PRs, and issues. UI strings live in
   `locales/` (`en` / `de` / `ar` / `es` / `nl`).
2. **Never name a competitor** anywhere public — irreversible once pushed.
3. **Never commit secrets** — keys go in `.env` (ignored) or encrypted DB columns later.
4. **Stay inside the issue.** Extra ideas go in `IDEAS.md` or a new issue, not this branch.
5. **Milestone 0 first:** web chat streams and cancels; conversation is stored. Messaging
   channels next. **Phone is last (Milestone 11).** Write the conversation layer to voice
   constraints from day one (streaming, cancel, latency).
6. **Everything streams.** Target <800 ms end-of-speech → first audio. Never wait for a
   full LLM reply before starting TTS. `cancel()` on TTS is mandatory.

---

## Repository layout

| Path | Role |
|---|---|
| `agent/` | Soft real-time conversation / call path (Python). Never serves the dashboard. |
| `api/` | FastAPI REST + WebSocket. Never touches audio. |
| `web/` | Next.js dashboard. Talks **only** to `api/`. |
| `locales/` | UI strings, five committed languages. One translated file per PR for first contributions. |
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

## Where each agent starts

Every entry file below points back here. `AGENTS.md` and `CLAUDE.md` are the only two
that carry content — the rest are pointers, so the rules can never drift apart.

| Agent | Reads |
|---|---|
| Claude Code | `CLAUDE.md` (full contract) |
| OpenAI Codex, Cursor, Windsurf, Zed, Aider, Jules | `AGENTS.md` |
| Google Antigravity, Gemini CLI | `GEMINI.md` → `AGENTS.md` |
| GitHub Copilot | `.github/copilot-instructions.md` → `AGENTS.md` |

Using an agent that reads none of these? Point it at `AGENTS.md` by hand and open a PR
adding its entry file as a one-line pointer.

---

## Working alongside other agents

More than one coding agent works on this repository at the same time (Claude Code,
OpenAI Codex, Google Antigravity, and sometimes a human contributor). They do not see
each other. The branches and pull requests on GitHub are the only shared state, so:

1. **One task, one branch, one PR, your own worktree.** Never edit `main` directly.
   `git worktree add ../tel_agent-<task> -b <prefix>/<task>` keeps agents from
   stepping on each other's working trees.
2. **Say who you are.** The first line of every PR description is
   `Agent: claude-code` / `Agent: codex` / `Agent: antigravity` / `Agent: human`.
3. **Check for an open PR on the same files before you start.**
   `gh pr list --repo Dpro-at/Tel-Agent --json number,title,headRefName,files`
   If one exists, do not open a second — comment on it or ask the maintainer.
4. **Never review your own PR.** Ask another agent, or the maintainer, for the review.
5. **The maintainer merges.** Do not merge, do not force-push, do not delete branches
   that are not yours.
6. **Commits carry the maintainer's name alone** — no `Co-Authored-By` trailer of any
   kind, whatever your harness suggests.
7. **No key ever appears in a PR, a comment, or a chat.** If a task needs one, stop
   and say so; the maintainer enters keys in the product's settings screens.

The maintainer keeps a private board of which agent holds which task. If you were
given a task from it, the task's *Done when* is your acceptance test — nothing more.

## Cursor-specific rules

Scoped project rules live in [`.cursor/rules/`](.cursor/rules/). They refine this file
for `web/`, Python paths, and `locales/` — they do not replace `CLAUDE.md`.
