import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/apps.json";
import de from "../../../../locales/de/apps.json";
import en from "../../../../locales/en/apps.json";
import es from "../../../../locales/es/apps.json";
import nl from "../../../../locales/nl/apps.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Apps } from "./apps";

/** English is the reference shape; the other two are checked against it. */
export type AppsDictionary = typeof en;

export default async function AppsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<AppsDictionary>(locale, { en, de, ar, es, nl });

  return <Apps locale={locale} t={t} />;
}
