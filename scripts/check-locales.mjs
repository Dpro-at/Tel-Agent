#!/usr/bin/env node
/**
 * Compares every locale in `locales/` against English and reports what is missing.
 *
 * English is the source language: a key exists because it exists in `en/`. A key
 * present in another locale and absent from English is not a translation, it is a
 * leftover, and it is reported too.
 *
 * A directory holding nothing but `.gitkeep` is a language the project wants and
 * nobody has started. It costs nothing, it is honest about standing at 0%, and it is
 * what makes the work visible to somebody looking for a first task.
 *
 * No dependencies. Run it with:
 *
 *   node scripts/check-locales.mjs               # summary, every language
 *   node scripts/check-locales.mjs --locale fr   # one language, file by file
 *   node scripts/check-locales.mjs --list        # every missing key, not just counts
 *
 * Two tiers, as in locales/README.md. Only the committed locales gate anything: this
 * exits 1 when a committed locale (de, ar, es, nl) is missing a key, and 0 when a community language is
 * behind — falling behind is precisely what a community locale is allowed to do.
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const LOCALES_DIR = join(ROOT, "locales");
const SOURCE = "en";

/** Kept current by the project. A gap in one of these fails the check. */
const COMMITTED = new Set(["en", "de", "ar", "es", "nl"]);

const args = process.argv.slice(2);
const showAll = args.includes("--list");
// indexOf returns -1 when the flag is absent, and args[0] is not the locale.
const onlyLocale = args.includes("--locale") ? args[args.indexOf("--locale") + 1] : undefined;

/** Every leaf key in an object, as dotted paths. Arrays count as leaves. */
function leaves(obj, prefix = "") {
  const out = [];
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      out.push(...leaves(value, path));
    } else {
      out.push(path);
    }
  }
  return out;
}

function read(locale, file) {
  const path = join(LOCALES_DIR, locale, file);
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (error) {
    console.error(`  ✗ ${locale}/${file} is not valid JSON — ${error.message}`);
    process.exit(2);
  }
}

const localeDirs = readdirSync(LOCALES_DIR, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name);

if (!localeDirs.includes(SOURCE)) {
  console.error(`No ${SOURCE}/ directory. English is the source language.`);
  process.exit(2);
}

const sourceFiles = readdirSync(join(LOCALES_DIR, SOURCE)).filter((f) => f.endsWith(".json"));
const targets = localeDirs.filter((l) => l !== SOURCE && (!onlyLocale || l === onlyLocale));

if (onlyLocale && !targets.length) {
  const smallest = readdirSync(join(LOCALES_DIR, SOURCE))
    .filter((f) => f.endsWith(".json"))
    .map((f) => ({ f, n: leaves(JSON.parse(readFileSync(join(LOCALES_DIR, SOURCE, f), "utf8"))).length }))
    .sort((a, b) => a.n - b.n)[0];

  console.log(`\n  ${onlyLocale} does not exist yet. Start it with one file.\n`);
  console.log(`    mkdir -p locales/${onlyLocale}`);
  console.log(`    cp locales/${SOURCE}/${smallest.f} locales/${onlyLocale}/`);
  console.log(`\n  Then translate the values in that one file — ${smallest.n} strings — and open a`);
  console.log(`  pull request. Do not copy all ${sourceFiles.length} files: an untranslated copy of`);
  console.log(`  English reads as finished work and is harder to find than a missing file.\n`);
  console.log(`  A language is built one file at a time, by whoever turns up. It is only`);
  console.log(`  registered in LOCALES (web/lib/locales.ts) once this reports 100%, so a`);
  console.log(`  partial language breaks nothing and ships to nobody.\n`);
  process.exit(1);
}

// English totals first — everything is measured against these.
const sourceKeys = new Map();
let sourceTotal = 0;
for (const file of sourceFiles) {
  const keys = leaves(read(SOURCE, file) ?? {});
  sourceKeys.set(file, keys);
  sourceTotal += keys.length;
}

console.log(`\n  Source: ${SOURCE} — ${sourceTotal} keys across ${sourceFiles.length} files\n`);

let failed = false;
let unclaimed = 0;

for (const locale of targets) {
  const committed = COMMITTED.has(locale);
  const missing = [];
  const extra = [];
  const untranslated = [];
  const absentFiles = [];

  for (const file of sourceFiles) {
    const expected = sourceKeys.get(file);
    const target = read(locale, file);

    if (target === null) {
      absentFiles.push(file);
      missing.push(...expected.map((k) => `${file}:${k}`));
      continue;
    }

    const actual = new Set(leaves(target));
    for (const key of expected) {
      if (!actual.has(key)) missing.push(`${file}:${key}`);
    }
    for (const key of actual) {
      if (!expected.includes(key)) extra.push(`${file}:${key}`);
    }

    // Present, but identical to English — usually a copied file nobody translated.
    const source = read(SOURCE, file);
    for (const key of expected) {
      if (!actual.has(key)) continue;
      const a = key.split(".").reduce((o, k) => o?.[k], source);
      const b = key.split(".").reduce((o, k) => o?.[k], target);
      if (typeof a === "string" && a === b && a.trim() !== "") untranslated.push(`${file}:${key}`);
    }
  }

  const done = sourceTotal - missing.length;
  const percent = sourceTotal ? Math.round((done / sourceTotal) * 100) : 100;
  const bar = "█".repeat(Math.round(percent / 5)).padEnd(20, "░");
  const tier = committed ? "" : "   community";

  console.log(
    `  ${locale.padEnd(8)} ${bar} ${String(percent).padStart(3)}%   ${done}/${sourceTotal}${tier}`,
  );

  // Per-file breakdown. This is the pick-list: one file is one contribution, so a
  // contributor scans this and takes the smallest thing nobody has done.
  //
  // Every wanted language sits here as an empty directory, so printing all 33 rows for
  // each of them would bury the summary. The list is shown for the locales that gate a
  // release, and on request for any other.
  if (missing.length) {
    if (committed) failed = true;
    if (done === 0) unclaimed += 1;

    const detailed = committed || onlyLocale === locale || showAll;

    if (!detailed) {
      console.log(`           ${missing.length} open — check-locales.mjs --locale ${locale}\n`);
    } else {
      const perFile = sourceFiles
        .map((file) => {
          const total = sourceKeys.get(file).length;
          const gone = missing.filter((m) => m.startsWith(`${file}:`)).length;
          return { file, total, gone, absent: absentFiles.includes(file) };
        })
        .filter((row) => row.gone > 0)
        .sort((a, b) => a.total - b.total);

      console.log(`         ${missing.length} key(s) to translate, in ${perFile.length} file(s):\n`);
      for (const row of perFile) {
        const state = row.absent ? "not started" : `${row.total - row.gone}/${row.total} done`;
        console.log(`           ${String(row.total).padStart(4)} keys  ${row.file.padEnd(22)} ${state}`);
      }
      console.log();

      if (showAll) {
        for (const key of missing) console.log(`           - ${key}`);
        console.log();
      } else {
        console.log(`         Smallest first. Run with --list to see every key.\n`);
      }
    }
  }
  if (untranslated.length) {
    console.log(`         ${untranslated.length} key(s) identical to English — check these are intentional`);
    if (showAll) for (const key of untranslated) console.log(`           ~ ${key}`);
    console.log();
  }
  if (extra.length) {
    if (committed) failed = true;
    console.log(`         ${extra.length} key(s) not in English — leftovers, remove them`);
    if (showAll) for (const key of extra) console.log(`           + ${key}`);
    console.log();
  }
}

if (!failed) {
  console.log(`  Nothing missing in ${[...COMMITTED].join(" / ")}.\n`);
}
if (unclaimed) {
  console.log(`  ${unclaimed} language(s) at 0% — nobody has started them. One file is a`);
  console.log(`  complete contribution, and the smallest is 16 strings.\n`);
}
process.exit(failed ? 1 : 0);
