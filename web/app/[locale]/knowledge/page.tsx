import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/knowledge.json";
import de from "../../../../locales/de/knowledge.json";
import en from "../../../../locales/en/knowledge.json";
import es from "../../../../locales/es/knowledge.json";
import nl from "../../../../locales/nl/knowledge.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Knowledge } from "./knowledge";

/** English is the reference shape; the other two are checked against it. */
export type KnowledgeDictionary = typeof en;

export default async function KnowledgePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<KnowledgeDictionary>(locale, { en, de, ar, es, nl });

  return <Knowledge locale={locale} t={t} />;
}
