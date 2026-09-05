import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/home.json";
import de from "../../../../locales/de/home.json";
import en from "../../../../locales/en/home.json";
import es from "../../../../locales/es/home.json";
import nl from "../../../../locales/nl/home.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Home } from "./home";

/** English is the reference shape; the other two are checked against it. */
export type HomeDictionary = typeof en;

export default async function HomePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<HomeDictionary>(locale, { en, de, ar, es, nl });

  return <Home locale={locale} t={t} />;
}
