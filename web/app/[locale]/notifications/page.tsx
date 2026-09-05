import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/notifications.json";
import de from "../../../../locales/de/notifications.json";
import en from "../../../../locales/en/notifications.json";
import es from "../../../../locales/es/notifications.json";
import nl from "../../../../locales/nl/notifications.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Notifications } from "./notifications";

/** English is the reference shape; the other two are checked against it. */
export type NotificationsDictionary = typeof en;

export default async function NotificationsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<NotificationsDictionary>(locale, { en, de, ar, es, nl });

  return <Notifications locale={locale} t={t} />;
}
