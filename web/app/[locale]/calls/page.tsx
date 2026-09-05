import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/calls.json";
import de from "../../../../locales/de/calls.json";
import en from "../../../../locales/en/calls.json";
import es from "../../../../locales/es/calls.json";
import nl from "../../../../locales/nl/calls.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { CallsList } from "./calls-list";

/** English is the reference shape; the other two are checked against it. */
export type CallsDictionary = typeof en;

export default async function CallsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<CallsDictionary>(locale, { en, de, ar, es, nl });

  return <CallsList locale={locale} t={t} />;
}
