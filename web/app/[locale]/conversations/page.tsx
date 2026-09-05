import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/conversations.json";
import de from "../../../../locales/de/conversations.json";
import en from "../../../../locales/en/conversations.json";
import es from "../../../../locales/es/conversations.json";
import nl from "../../../../locales/nl/conversations.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Conversations } from "./conversations";

/** English is the reference shape; the other two are checked against it. */
export type ConversationsDictionary = typeof en;

export default async function ConversationsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<ConversationsDictionary>(locale, { en, de, ar, es, nl });

  return <Conversations locale={locale} t={t} />;
}
