#!/usr/bin/env node
/**
 * Checks that every translated string carries the same placeholders as its English
 * original.
 *
 * A placeholder is a `{name}` the application fills in at render time. Drop one and
 * the value it stood for is never shown — the user reads "No response from ." and the
 * host they needed is gone. Invent one and it renders literally, `{hsot}` and all.
 * Neither breaks the build, neither shows up in a coverage count, and both survive a
 * reviewer who does not read the language.
 *
 * This is deliberately not part of check-locales.mjs. That script measures how much of
 * English a locale has reached, and it lets a community language sit at 40% because
 * being unfinished is allowed. A broken placeholder is not slow progress, it is a bug
 * in a string that already shipped, so this gates every locale in the tree.
 *
 * Keys the locale has not translated yet are not this script's business — a key that
 * is absent cannot have the wrong placeholders, and check-locales.mjs already counts
 * it. Only keys present in both are compared.
 *
 * Placeholders are compared as sets, not as counts. Word order moves between languages
 * and a translation may reasonably mention a value once where English mentions it
 * twice; what matters is that nothing is dropped and nothing is invented.
 *
 * No dependencies. Run it with:
 *
 *   node scripts/check-placeholders.mjs
 *   node scripts/check-placeholders.mjs --locale es
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const LOCALES_DIR = join(ROOT, "locales");
const SOURCE = "en";

/** `{host}`, `{count}` — a single identifier in braces, which is all this project uses. */
const PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g;

const args = process.argv.slice(2);
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

function at(obj, key) {
  return key.split(".").reduce((o, k) => o?.[k], obj);
}

function placeholders(value) {
  return typeof value === "string" ? new Set(value.match(PLACEHOLDER) ?? []) : new Set();
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
  console.error(`No locales/${onlyLocale}/ directory.`);
  process.exit(2);
}

console.log(`\n  Comparing placeholders against ${SOURCE}/\n`);

let failed = false;
let checked = 0;

for (const locale of targets) {
  const problems = [];

  for (const file of sourceFiles) {
    const target = read(locale, file);
    if (target === null) continue;

    const source = read(SOURCE, file);

    for (const key of leaves(source ?? {})) {
      const translated = at(target, key);
      // Absent here means untranslated, which check-locales.mjs reports.
      if (typeof translated !== "string") continue;

      checked += 1;
      const expected = placeholders(at(source, key));
      const actual = placeholders(translated);

      const dropped = [...expected].filter((p) => !actual.has(p));
      const invented = [...actual].filter((p) => !expected.has(p));

      if (dropped.length || invented.length) {
        problems.push({ file, key, dropped, invented });
      }
    }
  }

  if (problems.length) {
    failed = true;
    console.log(`  ✗ ${locale} — ${problems.length} string(s) with the wrong placeholders\n`);
    for (const { file, key, dropped, invented } of problems) {
      console.log(`      ${file}:${key}`);
      if (dropped.length) console.log(`        dropped:  ${dropped.join(" ")}`);
      if (invented.length) console.log(`        invented: ${invented.join(" ")}`);
    }
    console.log();
  }
}

if (!failed) {
  console.log(`  ${checked} translated string(s) across ${targets.length} locale(s). Placeholders all match.\n`);
} else {
  console.log(`  A dropped placeholder loses the value it stood for; an invented one renders`);
  console.log(`  as literal text. Copy the braces from ${SOURCE}/ exactly — only the words`);
  console.log(`  around them get translated.\n`);
}

process.exit(failed ? 1 : 0);
