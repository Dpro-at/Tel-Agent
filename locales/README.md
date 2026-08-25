# locales/

Every user-facing string. **This is infrastructure, not content work.**

Multi-language is in from day one because retrofitting it is expensive: it means
touching every component that was built assuming English string lengths.

**English is the source language.** A key exists because it exists in `en/`. Strings are
written there first and every other locale is measured against it.

```bash
node scripts/check-locales.mjs              # where every language stands
node scripts/check-locales.mjs --locale fr  # one language, and what is left in it
node scripts/check-locales.mjs --list       # every missing key
```

---

## Two tiers, and the difference matters

| Tier | Locales | What the project promises |
|---|---|---|
| **Committed** | `en` · `de` · `ar` | Kept current. A new English string blocks a release until these three have it. |
| **Community** | everything else | Maintained by whoever turns up. Registered when complete, and allowed to fall behind without holding anything up. |

The tiers exist so that adding a language costs the project nothing. A committed locale
is a permanent obligation on every future change; a community locale is a contribution
that stands on its own. Without the split, every new language would make English harder
to change, and the honest response to "can we add twelve languages" would have to be no.

`de` is committed because the primary market is Austria — the realistic case, not a
translation afterthought. `ar` is committed because it is the RTL case, and RTL that is
not exercised is RTL that is broken.

---

## Adding a language

**One file at a time. Do not copy all 33.**

```bash
mkdir -p locales/fr
cp locales/en/code.json locales/fr/
# translate the values in that one file, then open a pull request
```

The smallest files are 16 to 26 strings — twenty minutes of work, and a complete
contribution on its own. `check-locales.mjs` prints the remaining files smallest-first,
so the next person can see what is left and take one.

**Why not copy everything at once.** An untranslated copy of English is worse than a
missing file: it looks finished, it ships English text to somebody who asked for their
own language, and nothing flags it. A missing file is visible in one command. The script
reports keys that are identical to English for the same reason.

**A partial language breaks nothing.** It is not added to `LOCALES` in
`web/lib/locales.ts` until `check-locales.mjs` reports it at 100%, so it is invisible to
users until it is real. Wiring it up is a maintainer's job, not part of a translation
pull request.

### Translating well

- **Translate the meaning, not the words.** If a literal translation reads oddly to a
  native speaker, it is wrong, whatever the dictionary says.
- **Leave `{placeholders}` exactly as they are.** `{count}` and `{name}` are filled at
  runtime; a translated placeholder is a bug that only shows up on that language.
- **Latin-script data is never translated** — phone numbers, API keys, timestamps, logs,
  code. Only interface text.
- **Product names stay** — Tel-Agent, WhatsApp, Telegram, SMS. If a key is identical to
  English on purpose, that is fine; the script only asks you to check, it does not
  demand a change.
- **Keep it short.** These are buttons and labels. A translation twice the length of the
  English breaks the layout it sits in.
- **Match the register of the English.** It is direct and unfussy; it does not apologise
  and it does not sell.

---

## What multi-language forces on the UI

**German runs about 30% longer than English.** No fixed-width buttons or labels
anywhere. Every layout must survive 1.4× string expansion without breaking.

**Arabic requires a mirrored layout** and mirrored directional icons — arrows,
chevrons. **But Latin-script data stays left-to-right**: phone numbers, API keys,
timestamps, logs, code. An Arabic transcript with an LTR timestamp column is exactly
the case to get right.

**Dates, times and number formats follow the selected locale**, not the browser.

---

## Rules

- No string is hardcoded in a component. If it can be read by a user, it lives here.
- Latin-script *data* is never translated — only interface text is.
- One file per screen per language, never one dictionary for everything: the screens are
  client components, so a single file would ship the whole product's copy to the browser
  on every route.
- A key added to `en/` and forgotten in `de/` or `ar/` is a `tsc` error, not a blank
  label at runtime — the German and Arabic dictionaries are passed where `typeof en` is
  expected. Community locales are checked by the script instead.
