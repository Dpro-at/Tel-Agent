import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/install.json";
import de from "../../../../locales/de/install.json";
import en from "../../../../locales/en/install.json";
import es from "../../../../locales/es/install.json";
import nl from "../../../../locales/nl/install.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { FirstRun } from "./first-run";

/** English is the reference shape; the other two are checked against it. */
export type InstallDictionary = typeof en;

export default async function InstallPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<InstallDictionary>(locale, { en, de, ar, es, nl });

  return <FirstRun locale={locale} t={t} />;
}
