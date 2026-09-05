import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/live.json";
import de from "../../../../locales/de/live.json";
import en from "../../../../locales/en/live.json";
import es from "../../../../locales/es/live.json";
import nl from "../../../../locales/nl/live.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Live } from "./live";

/** English is the reference shape; the other two are checked against it. */
export type LiveDictionary = typeof en;

export default async function LivePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<LiveDictionary>(locale, { en, de, ar, es, nl });

  return <Live locale={locale} t={t} />;
}
