import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/catalogue.json";
import de from "../../../../locales/de/catalogue.json";
import en from "../../../../locales/en/catalogue.json";
import es from "../../../../locales/es/catalogue.json";
import nl from "../../../../locales/nl/catalogue.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Catalogue } from "./catalogue";

/** English is the reference shape; the other two are checked against it. */
export type CatalogueDictionary = typeof en;

export default async function CataloguePage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<CatalogueDictionary>(locale, { en, de, ar, es, nl });

  return <Catalogue locale={locale} t={t} />;
}
