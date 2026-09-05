import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/workspace-new.json";
import de from "../../../../../locales/de/workspace-new.json";
import en from "../../../../../locales/en/workspace-new.json";
import es from "../../../../../locales/es/workspace-new.json";
import nl from "../../../../../locales/nl/workspace-new.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { NewWorkspace } from "./new-workspace";

/** English is the reference shape; the other two are checked against it. */
export type NewWorkspaceDictionary = typeof en;

export default async function NewWorkspacePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<NewWorkspaceDictionary>(locale, { en, de, ar, es, nl });

  return <NewWorkspace locale={locale} t={t} />;
}
