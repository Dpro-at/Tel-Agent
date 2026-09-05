import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/assistants.json";
import de from "../../../../locales/de/assistants.json";
import en from "../../../../locales/en/assistants.json";
import es from "../../../../locales/es/assistants.json";
import nl from "../../../../locales/nl/assistants.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Assistants } from "./assistants";

/** English is the reference shape; the other two are checked against it. */
export type AssistantsDictionary = typeof en;

export default async function AssistantsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<AssistantsDictionary>(locale, { en, de, ar, es, nl });

  return <Assistants locale={locale} t={t} />;
}
