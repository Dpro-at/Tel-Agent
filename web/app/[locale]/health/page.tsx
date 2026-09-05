import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/health.json";
import de from "../../../../locales/de/health.json";
import en from "../../../../locales/en/health.json";
import es from "../../../../locales/es/health.json";
import nl from "../../../../locales/nl/health.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Health } from "./health";

/** English is the reference shape; the other two are checked against it. */
export type HealthDictionary = typeof en;

export default async function HealthPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<HealthDictionary>(locale, { en, de, ar, es, nl });

  return <Health locale={locale} t={t} />;
}
