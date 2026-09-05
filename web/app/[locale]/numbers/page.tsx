import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/numbers.json";
import de from "../../../../locales/de/numbers.json";
import en from "../../../../locales/en/numbers.json";
import es from "../../../../locales/es/numbers.json";
import nl from "../../../../locales/nl/numbers.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Numbers } from "./numbers";

/** English is the reference shape; the other two are checked against it. */
export type NumbersDictionary = typeof en;

export default async function NumbersPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<NumbersDictionary>(locale, { en, de, ar, es, nl });

  return <Numbers locale={locale} t={t} />;
}
