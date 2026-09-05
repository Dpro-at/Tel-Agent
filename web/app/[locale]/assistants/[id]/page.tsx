import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/editor.json";
import de from "../../../../../locales/de/editor.json";
import en from "../../../../../locales/en/editor.json";
import es from "../../../../../locales/es/editor.json";
import nl from "../../../../../locales/nl/editor.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { AssistantEditor } from "./editor";

/** English is the reference shape; the other two are checked against it. */
export type EditorDictionary = typeof en;

export default async function AssistantEditorPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  if (!isLocale(locale)) notFound();

  // The id is a database key, so a link somebody typed by hand is refused here
  // rather than becoming a request the API has to answer.
  const assistantId = Number(id);
  if (!Number.isInteger(assistantId) || assistantId < 1) notFound();

  const t = pickDictionary<EditorDictionary>(locale, { en, de, ar, es, nl });

  return <AssistantEditor locale={locale} t={t} assistantId={assistantId} />;
}
