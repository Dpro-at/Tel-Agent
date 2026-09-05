import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/consent.json";
import de from "../../../../locales/de/consent.json";
import en from "../../../../locales/en/consent.json";
import es from "../../../../locales/es/consent.json";
import nl from "../../../../locales/nl/consent.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Consent } from "./consent";

/** English is the reference shape; the other two are checked against it. */
export type ConsentDictionary = typeof en;

export default async function ConsentPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<ConsentDictionary>(locale, { en, de, ar, es, nl });

  return <Consent locale={locale} t={t} />;
}
