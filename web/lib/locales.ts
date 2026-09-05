/**
 * Locale constants only - no dictionaries. The middleware runs on the edge and must
 * not pull three JSON files into its bundle just to know which prefixes are valid.
 */
export const LOCALES = ["en", "de", "ar", "es", "nl"] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";

export function isLocale(value: string): value is Locale {
  return (LOCALES as readonly string[]).includes(value);
}

export function getDirection(locale: Locale): "ltr" | "rtl" {
  return locale === "ar" ? "rtl" : "ltr";
}
