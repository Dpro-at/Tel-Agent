import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/backup.json";
import de from "../../../../locales/de/backup.json";
import en from "../../../../locales/en/backup.json";
import es from "../../../../locales/es/backup.json";
import nl from "../../../../locales/nl/backup.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Backup } from "./backup";

/** English is the reference shape; the other two are checked against it. */
export type BackupDictionary = typeof en;

export default async function BackupPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<BackupDictionary>(locale, { en, de, ar, es, nl });

  return <Backup locale={locale} t={t} />;
}
