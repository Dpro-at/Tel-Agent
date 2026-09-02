# Contributing to Tel-Agent

Thank you for looking at this. Please read the first section before opening a pull
request — it will save you time.

**First time contributing here? Go to [`docs/ONBOARDING.md`](docs/ONBOARDING.md)
instead.** It is one file, about thirty strings of translation, and it needs no setup —
the fastest way to run the whole fork-to-merge loop once before you take on anything
larger. Come back to this page afterwards.

---

## Read this first: the project is pre-alpha

Tel-Agent is at **Milestone 0**: getting a single conversation answered end to end in
a web chat, with the message captured and the transcript printed. There is no
installable release, no dashboard, and no database yet. The phone comes last, at
Milestone 11 — see [`docs/ROADMAP.md`](docs/ROADMAP.md) for why.

**Feature pull requests will be pointed at [`IDEAS.md`](IDEAS.md) rather than merged.**

That is not a judgement on your idea. The project follows one rule:

> **Nothing gets built until the agent answers on one channel, end to end.**
> Not the second channel. Not the rules engine. Not the UI beyond what it takes to
> watch one conversation happen.

The previous attempt at this stalled with plenty of plan and nothing a customer could
reach. The order is the only thing being changed this time, and holding that order is
what makes the project finishable. Good ideas go into `IDEAS.md`, where they wait rather than
distract.

**What is welcome right now:** bug reports, documentation fixes, corrections to the
specification, and anything that helps Milestone 0 work.

---

## What to work on

Work comes from the public board and nowhere else:

**https://github.com/orgs/Dpro-at/projects/6**

```bash
gh issue list --repo Dpro-at/Tel-Agent --label "good first issue" --state open
```

**Take from the `Ready` column only.** `Backlog` means the issue is blocked on another
one — it looks available and is not. Every issue carries a `level:` label, from
`level: first-issue` (small, finishable in one sitting) to `level: maintainer` (decides
architecture — please leave those).

Every issue is written the same way: **Why** it exists, **Do** what to build, **Done
when** the acceptance test, and **Verify** the exact commands a reviewer will run. If it
lists **Needs**, those issues must be closed first.

**Comment on the issue and wait to be assigned** before you write code. One person per
issue. If you have to stop, say so — that returns it to the pool the same day instead of
after the fourteen-day inactivity sweep, and nobody thinks less of you for it.

**The scope rule:** stay inside the issue. Something else that needs fixing goes in a
new issue, not in your branch. A pull request that does two things gets reviewed as
slowly as its harder half.

### If you work with an AI coding agent

The repository ships [`AGENTS.md`](AGENTS.md) for any coding agent, Cursor rules under
[`.cursor/rules/`](.cursor/rules/), and the whole workflow as a skill at
[`.claude/skills/contributing/`](.claude/skills/contributing/SKILL.md). An agent working
inside your clone picks these up automatically, so it already knows the branch naming, the
verification gate, and the rules below — you do not have to explain them.

Most issues also carry a ready-to-paste prompt and a rough time estimate in a comment.

Two things to hold your agent to, whichever one you use: **run the Verify commands and
look at the real output** before opening anything, and **do not let it widen the scope**
past the issue. Both are on you, not on the reviewer.

---

## The Contributor License Agreement

**Every contributor must accept the [CLA](CLA.md) before their first pull request is
merged.** No exceptions, including for one-line fixes.

You keep full ownership and copyright of your work. The CLA grants Dpro GmbH a licence
to use and relicense it, which is what allows Tel-Agent to be released under AGPL-3.0
while a hosted edition and a commercial licence exist alongside it.

To accept, add this line to your pull request description:

```
I have read the CLA document and I hereby sign the CLA.
```

---

## Everything in this repository is English

**All code, comments, docstrings, identifiers, commit messages and documentation are
written in English**, regardless of the language you speak. This project aims at an
international contributor base, and a codebase in any other language closes that door.

User-facing *interface strings* are the exception: those live in `locales/` and are
translated into `en`, `de` and `ar`. Translations are very welcome.

---

## Development setup

```bash
git clone https://github.com/Dpro-at/Tel-Agent.git
cd Tel-Agent
cp .env.example .env    # fill in your own keys
```

**Requirements**

- Python 3.11 or newer
- ffmpeg
- A number to test against — bought from a SIP provider and pointed at the agent.
  An extension on a PBX you administer works too, and is the better test of the
  on-premises path
- Your own API keys for speech-to-text, a language model, and text-to-speech

With a provider number the agent connects outwards and accepts no inbound
connections, so there is no NAT traversal and no RTP port range to open. If you
develop against a PBX instead, run on the **same LAN as it** — NAT and STUN are the
single biggest source of "the call connects but there is no audio".

Docker is not required and is not assumed anywhere in the code. All configuration
comes from environment variables.

### Running the checks locally

The same four checks that run on your pull request. Run them before you open one and
you will not be waiting on CI to tell you about a missing type hint.

```bash
pip install -e ".[dev]"

ruff format --check .        # formatting
ruff check .                 # linting, type-hint coverage, async discipline
lint-imports                 # agent/ must never import from api/
pytest                       # the test suite

cd web && npm ci && npm run lint && npm run typecheck
node scripts/check-locales.mjs   # en / de / ar must be complete
```

**Both database dialects.** `pytest` alone runs the suite against SQLite. Point
`TEST_POSTGRES_URL` at a PostgreSQL server and it runs against both — which is what CI
does, because D-029 supports two dialects and full-text search is implemented
differently on each.

```bash
TEST_POSTGRES_URL=postgresql+asyncpg://user:pass@localhost:5432/telagent_test pytest
```

No PostgreSQL on the machine? `docker-compose.dev.yml` starts a throwaway one with
matching credentials:

```bash
docker compose -f docker-compose.dev.yml up -d
TEST_POSTGRES_URL=postgresql+asyncpg://telagent:telagent@localhost:5432/telagent_test pytest
```

Without it you get the SQLite half and no failure. With it you get both.

**On Windows:** `lint-imports` crashes with a `UnicodeEncodeError` when its output is
redirected, because the console falls back to cp1252 and the progress spinner is not
ASCII. Set `PYTHONIOENCODING=utf-8` and it behaves. CI runs on Linux and never sees
this.

---

## Code conventions

**Python**

- Type hints on every public function
- `async`/`await` throughout the audio path — **a synchronous call on the call path is
  a bug even when it is fast today**
- Format with `ruff format`, lint with `ruff check`

**Secrets**

- Never log an API key, never commit one, never return one in full to a client
- New configuration goes in `.env.example` with a safe placeholder and a comment

**Call data**

Recordings and transcripts are personal data under GDPR. They stay on the machine that
produced them and are gitignored. Never add a real recording or transcript to a test
fixture.

**Commits**

English, imperative mood. Explain *why* in the body when the change is not obvious.

---

## What makes a good pull request here

- **One thing.** A PR that fixes a bug and reformats a file is two PRs.
- **Explains the why.** The what is visible in the diff.
- **Respects the latency budget.** Anything touching the call path is measured against
  §B4: under 800 ms from the end of caller speech to the first audio out. If your
  change adds latency there, say so and show the numbers.
- **Tested in German** if it touches speech. Quality drops noticeably outside English,
  and Austrian pronunciation of names and addresses is where most speech recognition
  fails. Testing only in English proves very little for this project.

---

## Reporting a security issue

**Do not open a public issue.** See [`SECURITY.md`](SECURITY.md).

---

## Where things are documented

| File | Contents |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | The complete specification — the single source of truth |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How `agent/`, `api/` and `web/` are separated, and which may talk to which |
| [`CLAUDE.md`](CLAUDE.md) | The development rules, including the build order |
| [`AGENTS.md`](AGENTS.md) | Short entry for AI coding agents (points at `CLAUDE.md`) |
| [`IDEAS.md`](IDEAS.md) | Everything deferred, and why |

When the specification and any other document disagree, the specification wins — and
the other document should be corrected.
