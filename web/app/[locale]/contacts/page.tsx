import { notFound } from "next/navigation";

import ar from "../../../../locales/ar/contacts.json";
import de from "../../../../locales/de/contacts.json";
import en from "../../../../locales/en/contacts.json";
import es from "../../../../locales/es/contacts.json";
import nl from "../../../../locales/nl/contacts.json";

import { pickDictionary } from "@/lib/i18n";
import { isLocale } from "@/lib/locales";

import { Contacts } from "./contacts";

/** English is the reference shape; the other two are checked against it. */
export type ContactsDictionary = typeof en;

export default async function ContactsPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const t = pickDictionary<ContactsDictionary>(locale, { en, de, ar, es, nl });

  return <Contacts locale={locale} t={t} />;
}
