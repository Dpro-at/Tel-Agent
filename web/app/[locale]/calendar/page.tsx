import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/calendar.json";
import de from "../../../../locales/de/calendar.json";
import en from "../../../../locales/en/calendar.json";
import es from "../../../../locales/es/calendar.json";
import nl from "../../../../locales/nl/calendar.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Calendar } from "./calendar";

/** English is the reference shape; the other two are checked against it. */
export type CalendarDictionary = typeof en;

export default async function CalendarPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<CalendarDictionary>(locale, { en, de, ar, es, nl });

  return <Calendar locale={locale} t={t} />;
}
