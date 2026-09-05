import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/connectors.json";
import de from "../../../../locales/de/connectors.json";
import en from "../../../../locales/en/connectors.json";
import es from "../../../../locales/es/connectors.json";
import nl from "../../../../locales/nl/connectors.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Connectors } from "./connectors";

/** English is the reference shape; the other two are checked against it. */
export type ConnectorsDictionary = typeof en;

export default async function ConnectorsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<ConnectorsDictionary>(locale, { en, de, ar, es, nl });

  return <Connectors locale={locale} t={t} />;
}
