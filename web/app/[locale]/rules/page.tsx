import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/rules.json";
import de from "../../../../locales/de/rules.json";
import en from "../../../../locales/en/rules.json";
import es from "../../../../locales/es/rules.json";
import nl from "../../../../locales/nl/rules.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { RoutingRules } from "./routing-rules";

/** English is the reference shape; the other two are checked against it. */
export type RulesDictionary = typeof en;

export default async function RulesPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<RulesDictionary>(locale, { en, de, ar, es, nl });

  return <RoutingRules locale={locale} t={t} />;
}
