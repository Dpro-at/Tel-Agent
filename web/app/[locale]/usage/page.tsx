import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/usage.json";
import de from "../../../../locales/de/usage.json";
import en from "../../../../locales/en/usage.json";
import es from "../../../../locales/es/usage.json";
import nl from "../../../../locales/nl/usage.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Usage } from "./usage";

/** English is the reference shape; the other two are checked against it. */
export type UsageDictionary = typeof en;

export default async function UsagePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<UsageDictionary>(locale, { en, de, ar, es, nl });

  return <Usage locale={locale} t={t} />;
}
