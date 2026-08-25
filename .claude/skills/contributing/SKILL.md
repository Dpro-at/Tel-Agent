---
name: contributing
description: Use when contributing to Tel-Agent - picking an issue to work on, setting up the repository for the first time, starting or finishing a task, opening a pull request, or asking "what can I work on" / "how do I start" / "is my change ready to submit". Covers the full path from a fresh clone to a merged pull request.
---

# Contributing to Tel-Agent

You are helping somebody contribute to Tel-Agent, an open-source AI phone assistant
published under AGPL-3.0 by Dpro GmbH.

**Read `CLAUDE.md` at the repository root before anything else.** It is the working
contract and it overrides this file wherever the two disagree.

## Before you touch anything

Three rules that are not negotiable, and breaking any of them wastes the maintainer's
time and the contributor's:

1. **Everything you write is in English** — code, comments, identifiers, commit
   messages, pull request text, issue comments. This is Rule 0 in `CLAUDE.md`. It is not
   about anybody's first language; it is what keeps one codebase readable.
2. **Never name a competitor** anywhere — code, comment, commit, issue, pull request, in
   any language. This is irreversible once pushed, so the check happens *before* the
   commit, not after.
3. **Never commit a secret** — no key, token, password, or real phone number. If one is
   needed to run something, it goes in `.env`, which is ignored.

## Step 1 — Find something to work on

Work comes from the public board and nowhere else. Do not invent a task, and do not
"just fix something you noticed while you were in there" — that goes in a new issue.

```bash
gh issue list --repo Dpro-at/Tel-Agent --label "good first issue" --state open
gh issue list --repo Dpro-at/Tel-Agent --label "help wanted" --state open
```

The board is at https://github.com/orgs/Dpro-at/projects/6

**Only take from the `Ready` column.** `Backlog` means blocked on something else; the
issue looks available and is not. Every issue carries a `level:` label:

| Label | What it means |
|---|---|
| `level: first-issue` | Small, self-contained, finishable in one sitting. Start here. |
| `level: easy` | Straightforward, little context needed. |
| `level: medium` | Needs some understanding of the codebase. |
| `level: hard` | Substantial, touches several parts. |
| `level: maintainer` | **Decides architecture. Do not take this one.** |

If the person you are helping has not contributed here before, steer them to
`level: first-issue`. There are real ones, not a token entry.

**Read the whole issue before starting.** Every issue is written with the same fields:
*Why* (the reason it exists), *Do* (what to build), *Done when* (the acceptance test),
*Verify* (the exact commands a reviewer will run), and sometimes *Needs* (issues that
must be closed first). If `Needs` lists something still open, this task is not ready —
go back to step 1.

## Step 2 — Claim it

```bash
gh issue comment <number> --repo Dpro-at/Tel-Agent --body "I would like to take this."
```

**Wait to be assigned before writing code.** One person per issue, and the assignment is
what stops two people building the same thing. If nobody responds within a few days, say
so on the issue rather than starting anyway.

## Step 3 — Set up, once

If the person you are helping has never worked on this repository, walk them through
this in order and **confirm each command actually worked** before moving on. Do not
paste all of it at once and hope. Budget about half an hour the first time, and roughly
nothing every time after.

### 3.1 — What has to be installed

```bash
git --version      # any recent version
node --version     # v20 or newer. Next.js 16 and React 19 will not run on v18
npm --version
gh --version       # GitHub CLI - https://cli.github.com
python --version   # 3.12+, only needed for backend issues
```

`gh` is not optional here. Claiming an issue, opening the pull request and reading the
board all go through it. If it is missing, install it before anything else.

Python is only needed if the issue touches `api/` or `agent/`. A documentation or
frontend issue needs Node and nothing more.

### 3.2 — Log in to GitHub

```bash
gh auth status
```

If that says you are not logged in:

```bash
gh auth login
```

Choose **GitHub.com** → **HTTPS** → **Yes** to authenticate git → **Login with a web
browser**, then paste the one-time code it prints.

**This step is skipped more often than any other**, and it fails late rather than early:
everything looks fine until the first `gh` command, which then prints *"To get started
with GitHub CLI, please run: gh auth login"* instead of doing the thing. If any `gh`
command produces that message, this is why.

Reading the project board needs one extra scope, which the default login does not grant:

```bash
gh auth refresh -s read:project
```

### 3.3 — Fork and clone

Everyone contributes through a fork. Nobody gets write access to the repository, and
that is the same rule for everyone.

```bash
gh repo fork Dpro-at/Tel-Agent --clone
cd Tel-Agent
git remote -v          # expect: origin = your fork, upstream = Dpro-at
```

`gh repo fork --clone` sets `upstream` for you. If `git remote -v` does not show it:

```bash
git remote add upstream https://github.com/Dpro-at/Tel-Agent.git
```

### 3.4 — Check the email on your commits

```bash
git config user.email
```

If that address is not on your GitHub account — or your `@users.noreply.github.com` one
— your commits land **unlinked**: the work gets merged and your name is on nothing.

This is the single most common way somebody does real work here and does not appear in
the contributors list, and it is awkward to correct afterwards. Fix it now:

```bash
git config user.email "you@example.com"
```

### 3.5 — Run what exists

The frontend runs today:

```bash
npm --prefix web install
npm --prefix web run dev
```

Open `http://localhost:3000`. It redirects to a locale — `/en`, `/de` or `/ar` — and you
should land on the sign-in screen. **If you see that, the setup worked.** Every screen
has a state switcher pinned to the top in development; it is stripped from a production
build.

The screens read from static fixture modules, so they render without a server. Nothing
is wired to anything yet.

**The backend does not run, because it does not exist.** `agent/` and `api/` hold six
`.gitkeep` files and two READMEs, and building them is what most of the open issues are.
If an issue asks you to run something that is not there, re-read it — the task is
almost certainly to create it.

### 3.6 — Confirm the tooling before you touch code

```bash
npx --prefix web tsc --noEmit    # expect: no output
npm --prefix web run lint
```

Run these **before** your first edit, not after. If something is already red on a clean
clone, that is worth reporting as its own issue — and it is not yours to fix inside an
unrelated pull request.

## Step 4 — Do the work

```bash
git checkout -b <type>/<short-name>
```

Types: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/`. Example:
`feat/backend-skeleton`.

**Stay inside the issue.** A pull request that does two things gets reviewed as slowly
as its harder half. Something else that needs fixing goes in a new issue.

Commits follow Conventional Commits:

```
feat(api): add health endpoint
docs(contributing): add a what-to-work-on section
```

Match the code around you — its naming, its comment density, its idiom. A change that
reads like it was always there is the goal.

## Step 5 — Verify before you open anything

Run the issue's own **Verify** commands first, then the repository gate for whichever
half you touched:

```bash
# Python
ruff check . && ruff format --check . && mypy . && pytest

# web/
npm --prefix web run lint
npx --prefix web tsc --noEmit
npm --prefix web run build
```

Nothing you touched may go red.

**Then check by hand:**

- No secret, key, token, password or real phone number in the diff.
- No competitor named anywhere, in any language.
- Everything in English.
- If it is visible, screenshots in light and dark, and one in `ar` if the layout is
  direction-sensitive.

**Do not report the task finished until these actually pass.** If something fails and
you cannot fix it, say so plainly in the pull request — a known failure stated honestly
is useful; a silent one wastes a review cycle.

## Step 6 — Open the pull request

```bash
git push -u origin <branch>
gh pr create --repo Dpro-at/Tel-Agent --title "<same as the commit>" --body "..."
```

The body says: what you did, what you deliberately did not do, and how you tested it.
Link the issue with `Closes #<number>`. Paste the output of the Verify commands.

Open it as a **draft** while it is not ready. Ready for review means you believe it is
done.

**The CLA.** The first pull request cannot be merged until its author has signed the
Contributor License Agreement. Expect to be asked.

## Step 7 — Review

Expect changes to be requested. It is not a verdict on the person.

Push follow-up commits to the same branch. **Do not force-push during a review** — the
reviewer loses their place in the diff.

It lands as a squash-merge, done by a maintainer. The branch is deleted automatically.

## If the work has to stop

Half the people who take an issue never finish, and that is normal. Saying so is a
contribution: it returns the task to the pool the same day instead of fourteen days
later, which is when an inactive assignment is cleared automatically.

```bash
gh issue comment <number> --repo Dpro-at/Tel-Agent --body "I cannot finish this - unassigning so somebody else can take it."
```

## What "done" means

The code works, the tests cover it, the documentation says so, and `.env.example` lists
any variable that was introduced. All four, not three.
