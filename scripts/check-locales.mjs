#!/usr/bin/env node
/**
 * Compares every locale in `locales/` against English and reports what is missing.
 *
 * English is the source language: a key exists because it exists in `en/`. A key
 * present in another locale and absent from English is not a translation, it is a
 * leftover, and it is reported too.
 *
 * No dependencies. Run it with:
 *
 *   node scripts/check-locales.mjs            # summary
 *   node scripts/check-locales.mjs --list     # every missing key
 *   node scripts/check-locales.mjs --locale fr
 *
 * Exits 1 if anything is missing, so it can be a CI check later.
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const LOCALES_DIR = join(ROOT, "locales");
const SOURCE = "en";

const args = process.argv.slice(2);
const showAll = args.includes("--list");
const onlyLocale = args[args.indexOf("--locale") + 1];

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

for (const locale of targets) {
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

  console.log(`  ${locale.padEnd(6)} ${bar} ${String(percent).padStart(3)}%   ${done}/${sourceTotal}`);

  // Per-file breakdown. This is the pick-list: one file is one contribution, so a
  // contributor scans this and takes the smallest thing nobody has done.
  if (missing.length) {
    failed = true;
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
  if (untranslated.length) {
    console.log(`         ${untranslated.length} key(s) identical to English — check these are intentional`);
    if (showAll) for (const key of untranslated) console.log(`           ~ ${key}`);
  }
  if (extra.length) {
    failed = true;
    console.log(`         ${extra.length} key(s) not in English — leftovers, remove them`);
    if (showAll) for (const key of extra) console.log(`           + ${key}`);
  }
  console.log();
}

if (!failed) {
  console.log(`  Nothing missing.\n`);
}
process.exit(failed ? 1 : 0);
