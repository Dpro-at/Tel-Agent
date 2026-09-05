import ar from "../../locales/ar/web.json";
import de from "../../locales/de/web.json";
import en from "../../locales/en/web.json";
import es from "../../locales/es/web.json";
import nl from "../../locales/nl/web.json";

import type { Locale } from "./locales";

export { DEFAULT_LOCALE, LOCALES, getDirection, isLocale } from "./locales";
export type { Locale } from "./locales";

/** English is the reference shape; every other locale is checked against it. */
export type Dictionary = typeof en;

const DICTIONARIES: Record<Locale, Dictionary> = {
  en,
  de: de as Dictionary,
  ar: ar as Dictionary,
  es: es as Dictionary,
  nl: nl as Dictionary,
};

export function getDictionary(locale: Locale): Dictionary {
  return DICTIONARIES[locale];
}

/** Replaces `{name}` placeholders. Kept deliberately small - no plural rules yet. */
export function interpolate(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in values ? String(values[key]) : match,
  );
}

/**
 * Per-screen dictionaries. Each screen ships its own JSON under `locales/<lang>/`
 * and its server page imports the three files directly, so a route only ever
 * bundles the strings it actually renders - not the whole product's copy.
 *
 * English is the reference: every other locale is typed against it, so a key added
 * in English and forgotten elsewhere is a build error rather than a blank label.
 */
export function pickDictionary<T>(
  locale: Locale,
  dictionaries: { en: T; de: T; ar: T; es: T; nl: T },
): T {
  return dictionaries[locale];
}

/**
 * Money, written the way each language writes it: EUR96.40 in English, 96,40 EUR
 * in Austrian German, and Latin digits with a trailing symbol in Arabic. Hard-coding
 * one format puts two conventions on the same screen, which is what this replaces.
 */
const CURRENCY_LOCALES: Record<Locale, string> = {
  en: "en-IE",
  de: "de-AT",
  ar: "ar-u-nu-latn",
  es: "es-ES",
  nl: "nl-NL",
};

export function formatCurrency(locale: Locale, value: number, currency = "EUR"): string {
  return new Intl.NumberFormat(CURRENCY_LOCALES[locale], { style: "currency", currency }).format(
    value,
  );
}
