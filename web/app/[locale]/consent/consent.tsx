"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { ConsentDictionary } from "./page";

type Key = keyof ConsentDictionary;

const COLUMNS =
  "minmax(0,1fr) minmax(0,1.6fr) minmax(0,1.3fr) minmax(0,1.5fr) 96px";

type Outcome = "booked" | "declined" | "no_answer" | "opted_out" | "skipped";

const BADGES: Record<Outcome, { label: Key; color: string; background: string; border: string }> = {
  booked: { label: "outcome_booked", color: "var(--od-green-text)", background: "rgba(63,185,132,.11)", border: "var(--od-green-border)" },
  declined: { label: "outcome_declined", color: "var(--od-text-5)", background: "var(--od-raise-6)", border: "var(--od-border-6)" },
  no_answer: { label: "outcome_no_answer", color: "var(--od-faint)", background: "transparent", border: "var(--od-border-3)" },
  opted_out: { label: "outcome_opted_out", color: "var(--od-red-text-4)", background: "rgba(240,96,94,.11)", border: "var(--od-red-border)" },
  skipped: { label: "outcome_skipped", color: "var(--od-amber-text)", background: "var(--od-amber-bg)", border: "var(--od-amber-border)" },
};

/** Names, numbers and dates are data; the day, campaign and basis are copy. */
const ROWS: {
  day: Key;
  time: string;
  name: string;
  number: string;
  campaign: Key;
  basis: Key;
  basisRef: string;
  basisRefKey?: Key;
  outcome: Outcome;
}[] = [
  { day: "day_today", time: "09:41", name: "Ingrid Bauer", number: "+43 664 338 1120", campaign: "campaign_autumn", basis: "basis_in_person", basisRef: "2026-03-04", outcome: "booked" },
  { day: "day_today", time: "09:39", name: "Peter Lang", number: "+43 699 771 4408", campaign: "campaign_autumn", basis: "basis_in_person", basisRef: "2025-11-18", outcome: "no_answer" },
  { day: "day_today", time: "09:36", name: "Sabine Reiter", number: "+43 650 220 9971", campaign: "campaign_autumn", basis: "basis_online", basisRef: "2026-01-22", outcome: "booked" },
  { day: "day_today", time: "09:31", name: "Franz Huber", number: "+43 676 445 2210", campaign: "campaign_autumn", basis: "basis_in_person", basisRef: "2024-09-02", outcome: "opted_out" },
  { day: "day_today", time: "09:28", name: "Maria Steiner", number: "+43 664 118 7742", campaign: "campaign_autumn", basis: "basis_none", basisRef: "", basisRefKey: "basis_ref_not_called", outcome: "skipped" },
  { day: "day_today", time: "09:24", name: "Klaus Wimmer", number: "+43 1 402 9930", campaign: "campaign_autumn", basis: "basis_online", basisRef: "2026-02-11", outcome: "declined" },
  { day: "day_yesterday", time: "14:52", name: "Elisabeth Mayr", number: "+43 1 402 8811", campaign: "campaign_followup", basis: "basis_service", basisRef: "", basisRefKey: "basis_ref_service", outcome: "booked" },
  { day: "day_yesterday", time: "14:47", name: "Josef Hofer", number: "+43 699 5567 903", campaign: "campaign_followup", basis: "basis_service", basisRef: "", basisRefKey: "basis_ref_service", outcome: "no_answer" },
];

const STATS: { label: Key; value: string; valueKey?: Key; note: Key }[] = [
  { label: "stat_attempts", value: "24 118", note: "stat_attempts_note" },
  { label: "stat_skipped", value: "1 204", note: "stat_skipped_note" },
  { label: "stat_opted_out", value: "41", note: "stat_opted_out_note" },
  { label: "stat_chain", value: "", valueKey: "stat_chain_value", note: "stat_chain_note" },
];

const FILTERS: { id: Outcome | "all"; label: Key }[] = [
  { id: "all", label: "filter_all" },
  { id: "opted_out", label: "outcome_opted_out" },
  { id: "skipped", label: "outcome_skipped" },
  { id: "booked", label: "outcome_booked" },
];

const DNC: { number: string; why: Key; when: string }[] = [
  { number: "+43 676 220 0043", why: "dnc_said_stop", when: "18 Aug" },
  { number: "+43 664 990 2214", why: "dnc_sms_stop", when: "16 Aug" },
  { number: "+43 1 555 0182", why: "dnc_by_hand", when: "11 Aug" },
];

const RETENTION: { label: Key; value: Key }[] = [
  { label: "retention_log", value: "retention_log_value" },
  { label: "retention_recordings", value: "retention_recordings_value" },
  { label: "retention_transcripts", value: "retention_transcripts_value" },
];

const SKELETON = [72, 88, 64, 92, 78, 70, 86, 60];

/** The list is longer than the three rows shown, so the count is stated separately. */
const DNC_TOTAL = 41;

export function Consent({ locale, t }: { locale: Locale; t: ConsentDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Outcome | "all">("all");

  const empty = state === "empty";
  const showBody = state === "default" || empty;
  const shown = ROWS.filter((row) => filter === "all" || row.outcome === filter);
  const hasRows = !empty && shown.length > 0;

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="consent" />
      </div>

      <StatePreview
        state={state}
        onChange={setState}
        states={["default", "empty", "loading", "error"]}
      />

      <div className="mx-auto max-w-[1400px] p-[26px_28px_90px]">
        <div className="text-od-faint text-[13px]">
          <Link href={`/${locale}/campaigns`}>{t.breadcrumb_campaigns}</Link>
          <span className="px-2">/</span>
          {t.title}
        </div>

        {state === "error" ? <ChainBroken locale={locale} t={t} /> : null}

        {state === "loading" ? (
          <div className="mt-5">
            <div
              className="h-[30px] w-[200px] rounded-md"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
            <div className="border-od-line bg-od-panel-deep-3 mt-[22px] overflow-hidden rounded-[10px] border">
              {SKELETON.map((width, index) => (
                <div
                  key={index}
                  className="grid gap-[18px] border-b border-[color:var(--od-raise-6)] p-[16px_18px]"
                  style={{ gridTemplateColumns: COLUMNS }}
                >
                  <div className="bg-od-raise-4 h-3 rounded" />
                  <div
                    className="h-3 rounded"
                    style={{
                      width: `${width}%`,
                      background:
                        "linear-gradient(90deg,var(--od-raise-2),var(--od-raise-11),var(--od-raise-2))",
                      backgroundSize: "420px 100%",
                      animation: "od-shimmer 1.4s linear infinite",
                    }}
                  />
                  <div className="bg-od-raise-4 h-3 rounded" />
                  <div className="bg-od-raise-4 h-3 rounded" />
                  <div className="bg-od-raise-8 h-5 rounded-[5px]" />
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {showBody ? (
          <div>
            <div className="mt-2 flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div className="max-w-[68ch]">
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                {/* Write-once, never edited: that is what makes it evidence. */}
                <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="border-od-border-7 text-od-muted hover:text-od-text-2 hover:bg-od-raise-4 cursor-pointer rounded-md border bg-transparent p-[8px_14px]"
                >
                  {t.verify_chain}
                </button>
                <button
                  type="button"
                  className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_15px] font-medium"
                >
                  {t.export_csv}
                </button>
              </div>
            </div>

            <div
              className="border-od-line bg-od-panel-deep-3 mt-5 grid overflow-hidden rounded-[10px] border"
              style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}
            >
              {STATS.map((stat) => (
                <div
                  key={stat.label}
                  className="p-[14px_18px]"
                  style={{ outline: "1px solid var(--od-line)" }}
                >
                  <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
                    {t[stat.label]}
                  </div>
                  {/* A counted value stays a numeral; a worded one is copy. */}
                  {stat.valueKey ? (
                    <div className="text-od-text mt-[6px] text-[19px]">{t[stat.valueKey]}</div>
                  ) : (
                    <div dir="ltr" className="mono ltr-data text-od-text mt-[6px] text-[19px]">
                      {stat.value}
                    </div>
                  )}
                  <div className="text-od-muted-5 mt-[3px] text-[12.5px] text-pretty">
                    {t[stat.note]}
                  </div>
                </div>
              ))}
            </div>

            <div className="mt-[18px] flex flex-wrap items-center gap-[10px]">
              <div className="border-od-border-6 bg-od-panel-deep-3 flex min-w-[240px] flex-[1_1_340px] items-center gap-[10px] rounded-lg border p-[10px_14px]">
                <span className="text-od-faint text-[15px]">⌕</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={t.search_placeholder}
                  className="text-od-text-2 min-w-0 flex-1 border-none bg-transparent text-[15px] outline-none"
                />
              </div>
              {FILTERS.map((entry) => {
                const on = filter === entry.id;
                return (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => setFilter(entry.id)}
                    className="cursor-pointer rounded-lg border p-[9px_13px] text-[13px] whitespace-nowrap"
                    style={{
                      borderColor: on ? "var(--od-stroke)" : "var(--od-border-7)",
                      background: on ? "var(--od-line-2)" : "var(--od-panel-deep-3)",
                      color: on ? "var(--od-text)" : "var(--od-muted)",
                    }}
                  >
                    {t[entry.label]}
                  </button>
                );
              })}
            </div>

            {hasRows ? (
              <div className="border-od-line bg-od-panel-deep-3 mt-4 overflow-x-auto overflow-y-hidden rounded-[10px] border">
                <div
                  className="border-od-line bg-od-canvas-2 text-od-faint grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
                  style={{ gridTemplateColumns: COLUMNS }}
                >
                  <span>{t.column_attempted}</span>
                  <span>{t.column_contact}</span>
                  <span>{t.column_campaign}</span>
                  <span>{t.column_basis}</span>
                  <span>{t.column_outcome}</span>
                </div>

                {shown.map((row) => {
                  const badge = BADGES[row.outcome];
                  return (
                    <div
                      key={`${row.day}-${row.time}`}
                      className="hover:bg-od-raise grid items-start gap-[18px] border-b border-[color:var(--od-raise-6)] p-[13px_18px]"
                      style={{ gridTemplateColumns: COLUMNS }}
                    >
                      <div>
                        <div className="text-od-text-3">{t[row.day]}</div>
                        <div dir="ltr" className="mono ltr-data text-od-faint mt-[2px] text-[12.5px]">
                          {row.time}
                        </div>
                      </div>
                      <div className="min-w-0">
                        <div className="text-od-text font-medium text-pretty">{row.name}</div>
                        <div
                          dir="ltr"
                          className="mono ltr-data text-od-muted-5 mt-[2px] text-[12.5px]"
                        >
                          {row.number}
                        </div>
                      </div>
                      <div className="text-od-text-5 min-w-0 text-pretty">{t[row.campaign]}</div>
                      <div className="min-w-0">
                        <div className="text-od-text-5 text-pretty">{t[row.basis]}</div>
                        {/* A consent date is a date; a reason is a sentence. */}
                        {row.basisRefKey ? (
                          <div className="text-od-faint mt-[2px] text-[12px]">
                            {t[row.basisRefKey]}
                          </div>
                        ) : (
                          <div
                            dir="ltr"
                            className="mono ltr-data text-od-faint mt-[2px] text-[12px]"
                          >
                            {row.basisRef}
                          </div>
                        )}
                      </div>
                      <div>
                        <span
                          className="inline-flex items-center rounded-md border p-[3px_9px] text-[12.5px] font-medium whitespace-nowrap"
                          style={{
                            borderColor: badge.border,
                            background: badge.background,
                            color: badge.color,
                          }}
                        >
                          {t[badge.label]}
                        </span>
                      </div>
                    </div>
                  );
                })}

                <div className="bg-od-canvas-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-[10px] p-[13px_18px]">
                  <span className="text-od-faint text-[12.5px]">
                    {interpolate(t.showing, { shown: shown.length, total: "24 118" })}
                  </span>
                  <button
                    type="button"
                    className="border-od-border-7 text-od-muted hover:text-od-text-2 hover:bg-od-raise-4 cursor-pointer rounded-md border bg-transparent p-[7px_13px] text-[13px]"
                  >
                    {t.load_older}
                  </button>
                </div>
              </div>
            ) : null}

            {empty ? (
              <div className="border-od-border-6 bg-od-panel-deep-2 mt-4 rounded-[10px] border border-dashed p-[48px_30px] text-center">
                <h3 className="text-od-text m-0 text-[19px] font-semibold">
                  Nothing has been called out yet
                </h3>
                <p className="text-od-muted mx-auto mt-[10px] max-w-[54ch] text-pretty">
                  The log fills itself the moment a campaign starts dialling. Inbound calls are not
                  recorded here — they have their own archive.
                </p>
                <div className="mt-[18px]">
                  <Link
                    href={`/${locale}/campaigns`}
                    className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 inline-block rounded-md border p-[9px_16px] font-medium hover:no-underline"
                  >
                    Back to campaigns
                  </Link>
                </div>
              </div>
            ) : null}

            <div className="mt-[22px] flex flex-wrap items-start gap-4">
              {/* An entry here can be annotated but never removed — that is the whole point. */}
              <div className="border-od-red-border-4 bg-od-red-bg-6 min-w-[min(100%,340px)] flex-[1_1_380px] rounded-[10px] border p-[18px]">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
                  <h2 className="m-0 text-[15.5px] font-semibold text-[color:var(--od-red-text-8)]">
                    {t.dnc_title}
                  </h2>
                  <span className="text-[12.5px] text-[color:var(--od-red-text-7)]">
                    {interpolate(t.dnc_count, { count: DNC_TOTAL })}
                  </span>
                </div>
                <p className="mt-[7px] text-[13px] text-pretty text-[color:var(--od-red-text-7)]">
                  {t.dnc_body}
                </p>
                <div className="mt-[14px] flex flex-col gap-[6px]">
                  {DNC.map((entry) => (
                    <div
                      key={entry.number}
                      className="border-od-red-border-4 bg-od-panel flex flex-wrap items-center gap-x-[14px] gap-y-2 rounded-lg border p-[9px_12px]"
                    >
                      <span
                        dir="ltr"
                        className="mono ltr-data text-od-text-3 min-w-0 flex-[1_1_130px] text-[13px]"
                      >
                        {entry.number}
                      </span>
                      <span className="text-od-muted-5 min-w-0 flex-[1_1_150px] text-[12.5px] text-pretty">
                        {t[entry.why]}
                      </span>
                      <span dir="ltr" className="mono ltr-data text-od-faint flex-none text-[12px]">
                        {entry.when}
                      </span>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className="border-od-red-border-2 hover:bg-od-red-bg-5 mt-3 cursor-pointer rounded-[7px] border border-dashed bg-transparent p-[8px_14px] text-[13px] text-[color:var(--od-red-text-2)]"
                >
                  {t.dnc_add}
                </button>
              </div>

              <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,300px)] flex-[1_1_320px] rounded-[10px] border p-[18px]">
                <h2 className="text-od-text m-0 text-[15.5px] font-semibold">
                  {t.retention_title}
                </h2>
                <div className="mt-[14px] flex flex-col gap-3">
                  {RETENTION.map((entry) => (
                    <div
                      key={entry.label}
                      className="border-od-border flex flex-wrap items-baseline justify-between gap-x-[14px] gap-y-[6px] border-b pb-3"
                    >
                      <span className="text-od-text-5 min-w-0 flex-[1_1_150px] text-pretty">
                        {t[entry.label]}
                      </span>
                      <span className="text-od-text-3 flex-none text-[13px]">
                        {t[entry.value]}
                      </span>
                    </div>
                  ))}
                </div>
                <p className="text-od-faint mt-3 text-[12.5px] text-pretty">
                  {t.retention_note}
                </p>
                <Link href={`/${locale}/settings`} className="mt-3 inline-block text-[13px]">
                  {t.retention_link}
                </Link>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ChainBroken({ locale, t }: { locale: Locale; t: ConsentDictionary }) {
  return (
    <div className="flex justify-center py-[70px]">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.error_label}
        </div>
        <h2 className="text-od-text mt-[18px] mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        {/* Each entry is signed with the one before it, so the break has a date. */}
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">{t.error_body}</p>
        <div className="mt-5 flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            {t.error_action}
          </button>
          <Link
            href={`/${locale}/health`}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 rounded-md border bg-transparent p-[9px_16px] hover:no-underline"
          >
            {t.error_diagnostics}
          </Link>
        </div>
        <div
          dir="ltr"
          className="border-od-border mono ltr-data text-od-faint mt-[18px] flex flex-wrap gap-4 border-t pt-[14px] text-[11.5px]"
        >
          <span>consent-log/chain-broken</span>
          <span>entry 24118</span>
          <span>2026-08-14 16:02:41</span>
        </div>
      </div>
    </div>
  );
}
