import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/screens.json";
import de from "../../../../locales/de/screens.json";
import en from "../../../../locales/en/screens.json";
import es from "../../../../locales/es/screens.json";
import nl from "../../../../locales/nl/screens.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Screens } from "./screens";

/** English is the reference shape; the other two are checked against it. */
export type ScreensDictionary = typeof en;

export default async function ScreensPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<ScreensDictionary>(locale, { en, de, ar, es, nl });

  return <Screens locale={locale} t={t} />;
}
