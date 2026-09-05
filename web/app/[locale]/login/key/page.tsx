import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/key-sign-in.json";
import de from "../../../../../locales/de/key-sign-in.json";
import en from "../../../../../locales/en/key-sign-in.json";
import es from "../../../../../locales/es/key-sign-in.json";
import nl from "../../../../../locales/nl/key-sign-in.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { KeySignIn } from "./key-sign-in";

/** English is the reference shape; the other two are checked against it. */
export type KeyDictionary = typeof en;

export default async function KeyPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<KeyDictionary>(locale, { en, de, ar, es, nl });

  return <KeySignIn locale={locale} t={t} />;
}
