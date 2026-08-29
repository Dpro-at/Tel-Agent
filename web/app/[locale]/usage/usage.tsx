"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { formatCurrency, interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { UsageDictionary } from "./page";

type Key = keyof UsageDictionary;

/** Calls per day this month. Zeroes are weekends and days that have not happened yet. */
const DAYS = [4, 9, 6, 0, 0, 11, 14, 7, 5, 3, 0, 0, 9, 12, 8, 10, 6, 0, 0, 13, 7, 4, 2, 0, 0, 0, 0, 0, 0, 0, 0];
const AGENT_SHARE = 0.78;
const CAP = 180;

export function Usage({ locale, t }: { locale: Locale; t: UsageDictionary }) {
  const euro = (value: number) => formatCurrency(locale, value);
  // "empty" is a month with no calls; "error" stands in for over budget.
  const [state, setState] = useState<ScreenState>("default");

  const empty = state === "empty";
  const over = state === "error";
  const loading = state === "loading";
  const showUsage = state === "default" || empty || over;

  const spend = empty ? 0 : over ? 214.6 : 96.4;
  const fraction = Math.min(spend / CAP, 1);
  const ringColor = over ? "var(--od-amber-text)" : "var(--od-violet)";
  const max = Math.max(...DAYS) || 1;

  const costs: { label: Key; detail: Key; amount: number; share: number; color: string }[] = empty
    ? []
    : [
        {
          label: "cost_inbound",
          detail: "cost_inbound_detail",
          amount: 4.53,
          share: 0.05,
          color: "var(--od-violet)",
        },
        {
          label: "cost_outbound",
          detail: "cost_outbound_detail",
          amount: 1.9,
          share: 0.02,
          color: "var(--od-violet-3)",
        },
        {
          label: "cost_whatsapp",
          detail: "cost_whatsapp_detail",
          amount: 40.32,
          share: 0.42,
          color: "var(--od-green)",
        },
        {
          label: "cost_sms",
          detail: "cost_sms_detail",
          amount: 9.6,
          share: 0.1,
          color: "var(--od-amber)",
        },
        {
          label: "cost_numbers",
          detail: "cost_numbers_detail",
          amount: 2.05,
          share: 0.02,
          color: "var(--od-stroke-5)",
        },
        {
          label: "cost_fallback",
          detail: over ? "cost_fallback_detail_over" : "cost_fallback_detail",
          amount: over ? 156.2 : 38.0,
          share: over ? 0.73 : 0.39,
          color: "var(--od-red-text-4)",
        },
      ];

  const assistants: { name: Key; meta: Key; amount: number; share: number }[] = [
    { name: "assistant_reception", meta: "assistant_reception_meta", amount: 61.4, share: 1 },
    { name: "assistant_night", meta: "assistant_night_meta", amount: 22.8, share: 0.37 },
    { name: "assistant_outbound", meta: "assistant_outbound_meta", amount: 12.2, share: 0.2 },
  ];

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="settings" />
      </div>

      <StatePreview
        state={state}
        onChange={setState}
        states={["default", "empty", "loading", "error"]}
        labels={{ error: "Over budget" }}
      />

      {over ? (
        <div className="border-od-amber-border-2 flex flex-wrap items-center gap-[14px] border-b bg-[var(--od-amber-bg-2)] px-7 py-[14px]">
          <span className="size-[10px] rounded-full bg-[color:var(--od-amber)]" />
          <div className="min-w-[240px] flex-[1_1_340px]">
            <div className="text-[15px] font-semibold text-[color:var(--od-amber-text-2)]">
              {t.over_title}
            </div>
            {/* The cap warns; it never stops a call. That promise is stated here. */}
            <div className="mt-[3px] text-[color:var(--od-amber-text-3)]">
              {t.over_body_before}
              <span className="mono">{euro(214.6)}</span>
              {t.over_body_middle}
              <span className="mono">{euro(CAP)}</span>
              {t.over_body_after}
            </div>
          </div>
          <button
            type="button"
            className="border-od-amber-border bg-od-amber-bg cursor-pointer rounded-md border p-[8px_14px] font-medium whitespace-nowrap text-[color:var(--od-amber-text-2)]"
          >
            {t.over_action}
          </button>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1180px] p-[26px_28px_140px]">
        {loading ? (
          <div>
            <div
              className="h-[30px] w-[180px] rounded-md"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
            <div className="mt-[26px] flex flex-wrap gap-4">
              {[
                { flex: "2 1 520px" },
                { flex: "1 1 280px" },
              ].map((entry, index) => (
                <div
                  key={index}
                  className="border-od-raise-12 h-[300px] rounded-xl border"
                  style={{
                    flex: entry.flex,
                    background:
                      "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
                    backgroundSize: "420px 100%",
                    animation: "od-shimmer 1.4s linear infinite",
                  }}
                />
              ))}
            </div>
          </div>
        ) : null}

        {showUsage ? (
          <div>
            <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-[14px]">
              <div className="max-w-[62ch]">
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  className="border-od-border-2 bg-od-panel text-od-text-2 hover:bg-od-raise inline-flex cursor-pointer items-center gap-[10px] rounded-lg border p-[9px_14px] whitespace-nowrap"
                >
                  {t.month} <span className="text-od-faint">▾</span>
                </button>
                <button
                  type="button"
                  className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-lg border bg-transparent p-[9px_14px] whitespace-nowrap"
                >
                  {t.export_csv}
                </button>
              </div>
            </div>

            <div className="mt-[22px] flex flex-wrap items-stretch gap-4">
              <div className="border-od-line bg-od-panel-deep-3 flex min-w-[min(100%,460px)] flex-[2_1_520px] flex-col rounded-xl border">
                <div className="border-od-line flex flex-wrap border-b">
                  {[
                    {
                      label: t.stat_calls,
                      value: empty ? "0" : "148",
                      note: empty ? t.stat_calls_note_empty : t.stat_calls_note,
                    },
                    {
                      label: t.stat_minutes,
                      value: empty ? "0" : "412",
                      note: empty ? t.stat_none : t.stat_minutes_note,
                    },
                    {
                      label: t.stat_spend,
                      value: empty ? euro(0) : euro(spend),
                      note: empty ? t.stat_none : t.stat_spend_note,
                    },
                  ].map((entry) => (
                    <div
                      key={entry.label}
                      className="min-w-[130px] flex-[1_1_150px] border-s border-[color:var(--od-raise-6)] p-[16px_18px]"
                    >
                      <div className="text-od-faint text-[12.5px]">{entry.label}</div>
                      <div
                        dir="ltr"
                        className="mono ltr-data text-od-text mt-[5px] text-[25px] font-semibold tracking-[-0.02em]"
                      >
                        {entry.value}
                      </div>
                      <div className="text-od-muted-5 mt-[3px] text-[12.5px]">{entry.note}</div>
                    </div>
                  ))}
                </div>

                <div className="flex-[1_1_auto] p-[18px_18px_14px]">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
                    <div className="text-od-faint text-[12px] font-semibold tracking-[.08em] uppercase">
                      {t.calls_per_day}
                    </div>
                    {!empty ? (
                      <div className="flex flex-wrap gap-[14px]">
                        {[
                          { label: t.legend_agent, color: "var(--od-violet)" },
                          { label: t.legend_person, color: "var(--od-stroke-5)" },
                        ].map((entry) => (
                          <span
                            key={entry.label}
                            className="text-od-muted-5 inline-flex items-center gap-[7px] text-[12.5px] whitespace-nowrap"
                          >
                            <span
                              className="size-[9px] rounded-[2px]"
                              style={{ background: entry.color }}
                            />
                            {entry.label}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>

                  {empty ? (
                    <div className="border-od-border-6 bg-od-panel-deep-2 text-od-muted mt-4 flex h-[168px] items-center justify-center rounded-[10px] border border-dashed p-5 text-center text-[13px] text-pretty">
                      {t.chart_empty}
                    </div>
                  ) : (
                    <div>
                      {/* First of the month to last, left to right, in every language. */}
                      <div
                        dir="ltr"
                        className="border-od-border-2 mt-4 flex h-[168px] items-end gap-[3px] border-b pb-[6px]"
                      >
                        {DAYS.map((calls, index) => {
                          const height = (calls / max) * 148;
                          const agentHeight = Math.round(height * AGENT_SHARE);
                          return (
                            <div
                              key={index}
                              title={`${index + 1} Aug — ${calls} calls`}
                              className="flex h-full min-w-[4px] flex-[1_1_0] flex-col justify-end"
                            >
                              <span
                                className="block rounded-t-[3px]"
                                style={{
                                  height: `${Math.max(height - agentHeight, calls ? 2 : 0)}px`,
                                  background: "var(--od-stroke-5)",
                                }}
                              />
                              <span
                                className="block"
                                style={{
                                  height: `${agentHeight}px`,
                                  background: "var(--od-violet)",
                                  borderRadius: height - agentHeight > 1 ? 0 : "3px 3px 0 0",
                                }}
                              />
                            </div>
                          );
                        })}
                      </div>
                      <div
                        dir="ltr"
                        className="mono text-od-faint-2 mt-[7px] flex justify-between text-[11.5px]"
                      >
                        <span>1 Aug</span>
                        <span>10 Aug</span>
                        <span>20 Aug</span>
                        <span>31 Aug</span>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              <div className="border-od-line bg-od-panel-deep-3 flex max-w-[400px] min-w-[min(100%,280px)] flex-[1_1_300px] flex-col rounded-xl border p-[18px]">
                <div className="text-od-faint text-[12px] font-semibold tracking-[.08em] uppercase">
                  {t.budget_heading}
                </div>
                <div className="flex justify-center p-[20px_0_4px]">
                  <div
                    className="relative size-[180px] rounded-full"
                    style={{
                      background: `conic-gradient(${ringColor} ${fraction * 360}deg, var(--od-raise-4) 0)`,
                    }}
                  >
                    <div className="bg-od-panel-deep-3 absolute inset-[13px] flex flex-col items-center justify-center rounded-full">
                      <div
                        dir="ltr"
                        className="mono ltr-data text-od-text text-[27px] font-semibold tracking-[-0.02em]"
                      >
                        {euro(spend)}
                      </div>
                      <div className="text-od-muted-5 mt-[2px] text-[12.5px]">
                        {interpolate(t.budget_of, { cap: euro(CAP) })}
                      </div>
                    </div>
                  </div>
                </div>
                <div className="text-od-muted-4 mt-3 text-[13px] text-pretty">
                  {empty ? t.budget_note_empty : over ? t.budget_note_over : t.budget_note_ok}
                </div>
                <div className="mt-auto flex flex-wrap gap-2 pt-4">
                  <button
                    type="button"
                    className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_13px] text-[13px] font-medium whitespace-nowrap"
                  >
                    {t.edit_budget}
                  </button>
                  <button
                    type="button"
                    className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[8px_13px] text-[13px] whitespace-nowrap"
                  >
                    {t.email_at_80}
                  </button>
                </div>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-start gap-4">
              <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,380px)] flex-[1_1_420px] rounded-xl border">
                <div className="p-[16px_18px_8px]">
                  <div className="text-od-faint text-[12px] font-semibold tracking-[.08em] uppercase">
                    {t.costs_heading}
                  </div>
                </div>

                {empty ? (
                  <div className="border-od-border-6 bg-od-panel-deep-2 m-[8px_18px_18px] rounded-[10px] border border-dashed p-[26px_20px]">
                    <div className="text-[15px] font-semibold">{t.costs_empty_title}</div>
                    <div className="text-od-muted mt-[6px] max-w-[46ch] text-[13px] text-pretty">
                      {t.costs_empty_body}
                    </div>
                  </div>
                ) : (
                  <>
                    {costs.map((entry) => (
                      <div
                        key={entry.label}
                        className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-[color:var(--od-raise-6)] p-[13px_18px]"
                      >
                        <div className="min-w-[180px] flex-[1_1_200px]">
                          <div className="flex items-center gap-[9px]">
                            <span
                              className="size-2 flex-none rounded-[2px]"
                              style={{ background: entry.color }}
                            />
                            <span className="text-od-text-3 font-medium">{t[entry.label]}</span>
                          </div>
                          <div className="text-od-faint mt-1 text-[12.5px] text-pretty">
                            {t[entry.detail]}
                          </div>
                          <div className="mt-2 h-1 rounded-full bg-[var(--od-raise-4)]">
                            <span
                              className="block h-1 rounded-full"
                              style={{
                                width: `${Math.max(entry.share * 100, 2)}%`,
                                background: entry.color,
                              }}
                            />
                          </div>
                        </div>
                        <span className="mono ltr-data text-od-text-2 text-[15px] whitespace-nowrap">
                          {euro(entry.amount)}
                        </span>
                      </div>
                    ))}
                    <div className="border-od-line bg-od-canvas-2 flex justify-between gap-4 rounded-b-xl border-t p-[14px_18px]">
                      <span className="text-od-muted-4">{t.total}</span>
                      <span className="mono ltr-data text-od-text text-[15px] font-semibold whitespace-nowrap">
                        {euro(spend)}
                      </span>
                    </div>
                  </>
                )}
              </div>

              <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,340px)] flex-[1_1_380px] rounded-xl border">
                <div className="p-[16px_18px_8px]">
                  <div className="text-od-faint text-[12px] font-semibold tracking-[.08em] uppercase">
                    {t.assistants_heading}
                  </div>
                </div>

                {empty ? (
                  <div className="border-od-border-6 bg-od-panel-deep-2 m-[8px_18px_18px] rounded-[10px] border border-dashed p-[26px_20px]">
                    <div className="text-[15px] font-semibold">{t.assistants_empty_title}</div>
                    <div className="text-od-muted mt-[6px] max-w-[46ch] text-[13px] text-pretty">
                      {t.assistants_empty_body}
                    </div>
                  </div>
                ) : (
                  assistants.map((entry) => (
                    <div
                      key={entry.name}
                      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-[color:var(--od-raise-6)] p-[13px_18px]"
                    >
                      <div className="min-w-[170px] flex-[1_1_200px]">
                        <div className="text-od-text-3 font-medium">{t[entry.name]}</div>
                        <div className="text-od-faint mt-[3px] text-[12.5px]">{t[entry.meta]}</div>
                        <div className="mt-2 h-1 rounded-full bg-[var(--od-raise-4)]">
                          <span
                            className="block h-1 rounded-full bg-[color:var(--od-violet)]"
                            style={{ width: `${Math.max(entry.share * 100, 2)}%` }}
                          />
                        </div>
                      </div>
                      <span className="mono ltr-data text-od-text-2 text-[15px] whitespace-nowrap">
                        {euro(entry.amount)}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Why the list is short: local models cost nothing per call. */}
            <div className="border-od-border bg-od-panel-deep-2 mt-4 flex flex-wrap items-center justify-between gap-x-[18px] gap-y-[10px] rounded-[10px] border p-[14px_18px]">
              <div className="text-od-muted max-w-[74ch] text-[13px] text-pretty">
                {t.local_note}
              </div>
              <Link
                href={`/${locale}/settings`}
                className="text-od-violet text-[13px] whitespace-nowrap hover:underline"
              >
                {t.model_settings}
              </Link>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
