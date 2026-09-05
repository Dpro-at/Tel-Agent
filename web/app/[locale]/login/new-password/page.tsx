import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/new-password.json";
import de from "../../../../../locales/de/new-password.json";
import en from "../../../../../locales/en/new-password.json";
import es from "../../../../../locales/es/new-password.json";
import nl from "../../../../../locales/nl/new-password.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { NewPassword } from "./new-password";

/** English is the reference shape; the other two are checked against it. */
export type NewPasswordDictionary = typeof en;

export default async function NewPasswordPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<NewPasswordDictionary>(locale, { en, de, ar, es, nl });

  return <NewPassword locale={locale} t={t} />;
}
