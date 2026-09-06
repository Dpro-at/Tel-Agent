import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/install.json";
import de from "../../../../locales/de/install.json";
import en from "../../../../locales/en/install.json";
import es from "../../../../locales/es/install.json";
import nl from "../../../../locales/nl/install.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { SetupFlow } from "./setup-flow";

/** English is the reference shape; the other four are checked against it. */
export type InstallDictionary = typeof en;

export default async function InstallPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ step?: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<InstallDictionary>(locale, { en, de, ar, es, nl });

  // `?step=ai` / `?step=cloud` / `?step=local` opens a later step directly - for looking at the screens
  // without creating an account first. Harmless: every write behind them needs the
  // admin session, which only the account step can create.
  const { step } = await searchParams;
  const initial = step === "ai" || step === "cloud" || step === "local" ? step : "account";

  return <SetupFlow locale={locale} t={t} initialStep={initial} />;
}
