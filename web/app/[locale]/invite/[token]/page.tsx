import { notFound } from "next/navigation";

import ar from "../../../../../locales/ar/invite.json";
import de from "../../../../../locales/de/invite.json";
import en from "../../../../../locales/en/invite.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { AcceptInvite } from "./accept";

/** English is the reference shape; the other two are checked against it. */
export type InviteDictionary = typeof en;

export default async function InvitePage({
  params,
}: {
  params: Promise<{ locale: string; token: string }>;
}) {
  const { locale, token } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<InviteDictionary>(locale, { en, de, ar });

  return <AcceptInvite locale={locale} token={token} t={t} />;
}
