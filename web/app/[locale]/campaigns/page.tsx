import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/campaigns.json";
import de from "../../../../locales/de/campaigns.json";
import en from "../../../../locales/en/campaigns.json";
import es from "../../../../locales/es/campaigns.json";
import nl from "../../../../locales/nl/campaigns.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Campaigns } from "./campaigns";

/** English is the reference shape; the other two are checked against it. */
export type CampaignsDictionary = typeof en;

export default async function CampaignsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<CampaignsDictionary>(locale, { en, de, ar, es, nl });

  return <Campaigns locale={locale} t={t} />;
}
