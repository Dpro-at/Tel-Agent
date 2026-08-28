import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/call-detail.json";
import de from "../../../../../locales/de/call-detail.json";
import en from "../../../../../locales/en/call-detail.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { CallDetail } from "./call-detail";

/** English is the reference shape; the other two are checked against it. */
export type CallDetailDictionary = typeof en;

export default async function CallDetailPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();
  const conversationId = Number(id);
  if (!Number.isInteger(conversationId) || conversationId < 1) notFound();

  const t = pickDictionary<CallDetailDictionary>(locale, { en, de, ar });

  return <CallDetail locale={locale} t={t} id={conversationId} />;
}
