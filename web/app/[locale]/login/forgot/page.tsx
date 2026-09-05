import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/forgot.json";
import de from "../../../../../locales/de/forgot.json";
import en from "../../../../../locales/en/forgot.json";
import es from "../../../../../locales/es/forgot.json";
import nl from "../../../../../locales/nl/forgot.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Forgot } from "./forgot";

/** English is the reference shape; the other two are checked against it. */
export type ForgotDictionary = typeof en;

export default async function ForgotPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<ForgotDictionary>(locale, { en, de, ar, es, nl });

  return <Forgot locale={locale} t={t} />;
}
