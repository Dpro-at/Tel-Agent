import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/code.json";
import de from "../../../../../locales/de/code.json";
import en from "../../../../../locales/en/code.json";
import es from "../../../../../locales/es/code.json";
import nl from "../../../../../locales/nl/code.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Code } from "./code";

/** English is the reference shape; the other two are checked against it. */
export type CodeDictionary = typeof en;

export default async function CodePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<CodeDictionary>(locale, { en, de, ar, es, nl });

  return <Code locale={locale} t={t} />;
}
