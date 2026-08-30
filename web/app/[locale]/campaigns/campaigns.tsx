"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { CampaignsDictionary } from "./page";

type Key = keyof CampaignsDictionary;

const COLUMNS =
  "minmax(0,1.7fr) minmax(0,1fr) minmax(0,1fr) minmax(0,1.15fr) minmax(84px,max-content)";

type Status = "running" | "finished" | "scheduled" | "paused" | "draft";

const BADGES: Record<Status, { label: Key; color: string; background: string; border: string }> = {
  running: { label: "status_running", color: "var(--od-violet-3)", background: "rgba(139,124,255,.13)", border: "var(--od-violet-border)" },
  finished: { label: "status_finished", color: "var(--od-green-text)", background: "rgba(63,185,132,.11)", border: "var(--od-green-border)" },
  scheduled: { label: "status_scheduled", color: "var(--od-text-5)", background: "var(--od-raise-6)", border: "var(--od-border-6)" },
  paused: { label: "status_paused", color: "var(--od-amber-text)", background: "var(--od-amber-bg)", border: "var(--od-amber-border)" },
  draft: { label: "status_draft", color: "var(--od-faint)", background: "transparent", border: "var(--od-border-3)" },
};

/** Assistant names are proper names and stay as written in all three languages. */
const CAMPAIGNS: {
  name: Key;
  targets: Key;
  assistant: string;
  window: Key;
  result: Key;
  resultNote?: Key;
  status: Status;
}[] = [
  {
    name: "campaign_autumn",
    targets: "campaign_autumn_targets",
    assistant: "Rezeption Wagner",
    window: "window_weekdays",
    result: "campaign_autumn_result",
    resultNote: "campaign_autumn_note",
    status: "running",
  },
  {
    name: "campaign_followup",
    targets: "campaign_followup_targets",
    assistant: "Rezeption Wagner",
    window: "window_midday",
    result: "campaign_followup_result",
    resultNote: "campaign_followup_note",
    status: "finished",
  },
  {
    name: "campaign_renewal",
    targets: "campaign_renewal_targets",
    assistant: "Rezeption Wagner",
    window: "window_twice",
    result: "campaign_renewal_result",
    resultNote: "campaign_renewal_note",
    status: "scheduled",
  },
  {
    name: "campaign_checkin",
    targets: "campaign_checkin_targets",
    assistant: "Nachbetreuung",
    window: "window_six_days",
    result: "campaign_checkin_result",
    resultNote: "campaign_checkin_note",
    status: "paused",
  },
  {
    name: "campaign_yearly",
    targets: "campaign_yearly_targets",
    assistant: "Rezeption Wagner",
    window: "window_unset",
    result: "campaign_yearly_result",
    status: "draft",
  },
];

const SEGMENTS: { label: Key; value: string; color: string; width: number }[] = [
  { label: "segment_booked", value: "94", color: "var(--od-green)", width: 22.8 },
  { label: "segment_reached", value: "74", color: "var(--od-violet)", width: 18 },
  { label: "segment_no_answer", value: "48", color: "var(--od-stroke-3)", width: 11.6 },
  { label: "segment_skipped", value: "56", color: "var(--od-amber-border)", width: 13.6 },
];

const LIVE_STATS: { label: Key; value: string; note: Key }[] = [
  { label: "stat_called", value: "272 / 412", note: "stat_called_note" },
  { label: "stat_booked", value: "94", note: "stat_booked_note" },
  { label: "stat_opted_out", value: "7", note: "stat_opted_out_note" },
  { label: "stat_length", value: "01:12", note: "stat_length_note" },
  { label: "stat_spent", value: "€12.10", note: "stat_spent_note" },
];

const LIVE_ROWS: { name: string; number: string; stage: Key; elapsed: string; ringing: boolean }[] =
  [
    { name: "Ingrid Bauer", number: "+43 664 338 1120", stage: "stage_offering", elapsed: "00:48", ringing: false },
    { name: "Peter Lang", number: "+43 699 771 4408", stage: "stage_ringing", elapsed: "00:06", ringing: true },
    { name: "Sabine Reiter", number: "+43 650 220 9971", stage: "stage_confirming", elapsed: "01:34", ringing: false },
  ];

const STEPS: { id: string; label: Key }[] = [
  { id: "basics", label: "step_basics" },
  { id: "targets", label: "step_targets" },
  { id: "script", label: "step_script" },
  { id: "schedule", label: "step_schedule" },
];

const ASSISTANTS: { name: string; note: Key }[] = [
  { name: "Rezeption Wagner", note: "assistant_wagner_note" },
  { name: "Nachbetreuung", note: "assistant_followup_note" },
  { name: "Reception EN", note: "assistant_english_note" },
];

const TARGETS: { name: Key; note: Key; count: string }[] = [
  { name: "target_segment", note: "target_segment_note", count: "468" },
  { name: "target_tag", note: "target_tag_note", count: "312" },
  { name: "target_csv", note: "target_csv_note", count: "—" },
];

const GOALS: Key[] = ["goal_booked", "goal_answer", "goal_delivered"];

const PACE: { name: Key; note: Key }[] = [
  { name: "pace_one", note: "pace_one_note" },
  { name: "pace_three", note: "pace_three_note" },
  { name: "pace_six", note: "pace_six_note" },
];

const DAYS: Key[] = ["day_mo", "day_tu", "day_we", "day_th", "day_fr", "day_sa", "day_su"];

/** Placeholders the agent fills at call time - the same tokens in every language. */
const TOKENS = ["{{first_name}}", "{{last_visit}}", "{{business}}", "{{next_free_slot}}"];

const CALLER_ID = "+43 1 402 8811";
const CAMPAIGN_COUNT = 5;

function Radio({ on }: { on: boolean }) {
  return (
    <span
      className="size-[15px] flex-none rounded-full"
      style={{
        border: on ? "4.5px solid var(--od-violet)" : "1.5px solid var(--od-stroke-3)",
        background: on ? "var(--od-white)" : "transparent",
      }}
    />
  );
}

function Option({
  on,
  onClick,
  children,
}: {
  on: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full cursor-pointer items-center gap-[11px] rounded-[9px] border p-[12px_14px]"
      style={{
        borderColor: on ? "var(--od-violet-border)" : "var(--od-border-4)",
        background: on ? "var(--od-canvas-violet)" : "var(--od-panel-deep-3)",
      }}
    >
      <Radio on={on} />
      {children}
    </button>
  );
}

export function Campaigns({ locale, t }: { locale: Locale; t: CampaignsDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [createOpen, setCreateOpen] = useState(false);

  const offline = state === "offline";
  const empty = state === "empty";
  const loading = state === "loading";
  const showBody = state === "default" || empty || offline;

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="campaigns" />
      </div>

      <StatePreview state={state} onChange={setState} />

      {offline ? (
        <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-[14px]">
          <span
            className="size-[10px] flex-none rounded-full bg-[#F0605E]"
            style={{ animation: "od-ring 1.6s ease-out infinite" }}
          />
          <div className="min-w-[240px] flex-[1_1_340px]">
            <div className="text-[15px] font-semibold text-[color:var(--od-red-text)]">
              {t.offline_title}
            </div>
            {/* It stopped cleanly and knows where to resume. That is the reassurance. */}
            <div className="mt-[3px] text-[color:var(--od-red-text-2)]">
              {t.offline_body_before}
              <span className="mono">{t.offline_progress}</span>
              {t.offline_body_after}
            </div>
          </div>
          <button
            type="button"
            className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[8px_14px] font-medium text-[color:var(--od-red-text-3)]"
          >
            {t.offline_retry}
          </button>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1400px] p-[26px_28px_90px]">
        {state === "error" ? <ConsentLogFull locale={locale} t={t} /> : null}

        {loading ? (
          <div>
            <div
              className="h-[30px] w-[240px] rounded-md"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
            <div
              className="border-od-raise-12 mt-[22px] h-[210px] rounded-xl border"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
          </div>
        ) : null}

        {showBody ? (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div>
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                <div className="text-od-muted-4 mt-[5px]">
                  {empty
                    ? t.subtitle_empty
                    : offline
                      ? interpolate(t.subtitle_offline, { count: CAMPAIGN_COUNT })
                      : interpolate(t.subtitle, { count: CAMPAIGN_COUNT })}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Link
                  href={`/${locale}/consent`}
                  className="border-od-border-7 text-od-muted hover:text-od-text-2 rounded-md border bg-transparent p-[8px_14px] hover:bg-[var(--od-raise-4)] hover:no-underline"
                >
                  {t.consent_log}
                </Link>
                <button
                  type="button"
                  onClick={() => setCreateOpen(true)}
                  className="cursor-pointer rounded-md border border-[color:var(--od-violet-deep)] bg-[color:var(--od-violet-solid)] p-[8px_15px] font-medium text-white hover:bg-[color:var(--od-violet-deep)]"
                >
                  {t.new_campaign}
                </button>
              </div>
            </div>

            {/* Austrian law, stated before anything is configured. */}
            <div className="border-od-amber-border-2 mt-4 flex items-start gap-3 rounded-[9px] border bg-[var(--od-amber-bg-2)] p-[13px_16px]">
              <span className="mt-px flex-none text-[14px] text-[color:var(--od-amber)]">!</span>
              <div className="min-w-0">
                <div className="font-semibold text-[color:var(--od-amber-text-2)]">
                  {t.regulated_title}
                </div>
                <div className="mt-[3px] text-pretty text-[color:var(--od-amber-text-3)]">
                  {t.regulated_body}
                </div>
              </div>
            </div>

            {!empty && !offline ? (
              <div className="mt-[22px] overflow-hidden rounded-xl border border-[color:var(--od-violet-border)] bg-[var(--od-canvas-violet)]">
                <div className="flex flex-wrap items-start justify-between gap-x-[22px] gap-y-[14px] p-[18px_20px_16px]">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-[10px]">
                      <span
                        className="size-2 flex-none rounded-full bg-[color:var(--od-violet)]"
                        style={{ animation: "od-ring-violet 1.8s ease-out infinite" }}
                      />
                      <span className="text-od-text text-[17px] font-semibold">
                        {t.campaign_autumn}
                      </span>
                      <span className="rounded-[5px] border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.13)] p-[2px_9px] text-[11.5px] font-semibold text-[color:var(--od-violet-3)]">
                        {t.status_running}
                      </span>
                    </div>
                    <div className="mt-[6px] text-pretty text-[color:var(--od-muted-3)]">
                      {t.running_description}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] font-medium"
                    >
                      {t.pause}
                    </button>
                    <Link
                      href={`/${locale}/live`}
                      className="border-od-border-7 text-od-muted hover:text-od-text-2 rounded-md border bg-transparent p-[8px_14px] hover:no-underline"
                    >
                      {t.listen_in}
                    </Link>
                  </div>
                </div>

                <div className="p-[0_20px_4px]">
                  <div className="flex h-[9px] overflow-hidden rounded-full bg-[var(--od-raise-3)]">
                    {SEGMENTS.map((segment) => (
                      <span
                        key={segment.label}
                        title={`${t[segment.label]} ${segment.value}`}
                        style={{ width: `${segment.width}%`, background: segment.color }}
                      />
                    ))}
                  </div>
                  <div className="mt-[11px] flex flex-wrap gap-x-5 gap-y-2">
                    {SEGMENTS.map((segment) => (
                      <div key={segment.label} className="flex items-center gap-[7px]">
                        <span
                          className="size-2 flex-none rounded-[2px]"
                          style={{ background: segment.color }}
                        />
                        <span className="text-[13px] text-[color:var(--od-muted-3)]">
                          {t[segment.label]}
                        </span>
                        <span dir="ltr" className="mono ltr-data text-od-text-3 text-[13px]">
                          {segment.value}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <div
                  className="bg-od-panel-deep-4 mt-[18px] grid border-t border-[color:var(--od-violet-border)]"
                  style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}
                >
                  {LIVE_STATS.map((stat) => (
                    <div
                      key={stat.label}
                      className="p-[14px_18px]"
                      style={{ outline: "1px solid var(--od-violet-border)" }}
                    >
                      <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
                        {t[stat.label]}
                      </div>
                      <div dir="ltr" className="mono ltr-data text-od-text mt-[6px] text-[19px]">
                        {stat.value}
                      </div>
                      <div className="text-od-muted-5 mt-[3px] text-[12.5px]">{t[stat.note]}</div>
                    </div>
                  ))}
                </div>

                <div className="bg-od-panel-deep-2 border-t border-[color:var(--od-violet-border)] p-[16px_20px_18px]">
                  <div className="text-od-faint mb-[10px] text-[11px] tracking-[.08em] uppercase">
                    {t.on_the_line}
                  </div>
                  <div className="flex flex-col gap-2">
                    {LIVE_ROWS.map((row) => (
                      <Link
                        key={row.number}
                        href={`/${locale}/live`}
                        className="border-od-border-4 bg-od-panel hover:bg-od-raise flex flex-wrap items-center gap-x-[14px] gap-y-[10px] rounded-lg border p-[10px_13px] text-inherit hover:border-[color:var(--od-violet-border)] hover:no-underline"
                      >
                        <span
                          className="size-2 flex-none rounded-full"
                          style={{
                            background: row.ringing ? "var(--od-amber)" : "var(--od-violet)",
                            animation: row.ringing ? "none" : "od-ring-violet 1.8s ease-out infinite",
                          }}
                        />
                        <span className="text-od-text min-w-[130px] font-medium">{row.name}</span>
                        <span dir="ltr" className="mono ltr-data text-od-muted-5 text-[12.5px]">
                          {row.number}
                        </span>
                        <span className="min-w-0 flex-[1_1_160px] text-[13px] text-[color:var(--od-muted-3)]">
                          {t[row.stage]}
                        </span>
                        <span dir="ltr" className="mono ltr-data text-od-faint text-[12.5px]">
                          {row.elapsed}
                        </span>
                        <span className="ms-auto flex-none text-[12.5px] text-[color:var(--od-violet-2)]">
                          {t.listen_in_arrow}
                        </span>
                      </Link>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}

            {empty ? (
              <div className="border-od-border-6 bg-od-panel-deep-2 mt-[22px] rounded-[10px] border border-dashed p-[52px_30px] text-center">
                <h3 className="text-od-text m-0 text-[19px] font-semibold">{t.empty_title}</h3>
                <p className="text-od-muted mx-auto mt-[10px] max-w-[54ch] text-pretty">
                  {t.empty_body}
                </p>
                <button
                  type="button"
                  onClick={() => setCreateOpen(true)}
                  className="mt-5 cursor-pointer rounded-md border border-[color:var(--od-violet-deep)] bg-[color:var(--od-violet-solid)] p-[9px_17px] font-medium text-white hover:bg-[color:var(--od-violet-deep)]"
                >
                  {t.new_campaign}
                </button>
              </div>
            ) : (
              <div className="border-od-line bg-od-panel-deep-3 mt-6 overflow-x-auto overflow-y-hidden rounded-[10px] border">
                <div
                  className="border-od-line bg-od-canvas-2 text-od-faint grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
                  style={{ gridTemplateColumns: COLUMNS }}
                >
                  <span>{t.column_campaign}</span>
                  <span>{t.column_assistant}</span>
                  <span>{t.column_window}</span>
                  <span>{t.column_result}</span>
                  <span>{t.column_status}</span>
                </div>

                {CAMPAIGNS.map((campaign) => {
                  const badge = BADGES[campaign.status];
                  return (
                    <div
                      key={campaign.name}
                      className="hover:bg-od-raise grid items-start gap-[18px] border-b border-[color:var(--od-raise-6)] p-[14px_18px]"
                      style={{ gridTemplateColumns: COLUMNS }}
                    >
                      <div className="min-w-0">
                        <div className="text-od-text font-medium">{t[campaign.name]}</div>
                        <div className="text-od-muted-5 mt-[3px] text-[13px] text-pretty">
                          {t[campaign.targets]}
                        </div>
                      </div>
                      <div className="text-od-text-5">{campaign.assistant}</div>
                      <div className="text-od-muted text-[12.5px]">{t[campaign.window]}</div>
                      <div className="min-w-0">
                        <div className="text-od-text-5">{t[campaign.result]}</div>
                        {campaign.resultNote ? (
                          <div className="text-od-faint mt-[3px] text-[12.5px]">
                            {t[campaign.resultNote]}
                          </div>
                        ) : null}
                      </div>
                      <div>
                        <span
                          className="inline-flex items-center rounded-md border p-[3px_10px] text-[12.5px] font-medium whitespace-nowrap"
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
              </div>
            )}
          </div>
        ) : null}
      </div>

      {createOpen ? <CreateDialog t={t} onClose={() => setCreateOpen(false)} /> : null}
    </div>
  );
}

function CreateDialog({ t, onClose }: { t: CampaignsDictionary; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [assistant, setAssistant] = useState(0);
  const [target, setTarget] = useState(0);
  const [goal, setGoal] = useState(0);
  const [pace, setPace] = useState(1);
  const [days, setDays] = useState([1, 2, 3, 4, 5]);

  const id = STEPS[step].id;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center overflow-auto p-[40px_20px]"
      style={{ background: "var(--od-scrim)" }}
    >
      <div
        className="border-od-border-9 bg-od-panel w-full max-w-[760px] overflow-hidden rounded-[14px] border"
        style={{ boxShadow: "0 26px 70px var(--od-scrim-3)" }}
      >
        <div className="border-od-border flex items-start justify-between gap-4 border-b p-[20px_24px_16px]">
          <div>
            <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.wizard_title}</h2>
            <div className="text-od-muted-4 mt-1 text-[13px]">
              {interpolate(t.wizard_step, { step: step + 1, label: t[STEPS[step].label] })}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t.close}
            className="border-od-border-2 text-od-muted-4 hover:bg-od-raise hover:text-od-text size-[30px] flex-none cursor-pointer rounded-[7px] border bg-transparent text-[15px] leading-none"
          >
            ×
          </button>
        </div>

        <div className="border-od-border bg-od-panel-deep-3 flex gap-1 border-b p-[12px_24px]">
          {STEPS.map((entry, index) => {
            const on = step === index;
            return (
              <button
                key={entry.id}
                type="button"
                onClick={() => setStep(index)}
                className="cursor-pointer rounded-[7px] border p-[6px_12px] text-[13px]"
                style={{
                  borderColor: on ? "var(--od-border-9)" : "transparent",
                  background: on ? "var(--od-raise-7)" : "transparent",
                  color: on
                    ? "var(--od-text)"
                    : index < step
                      ? "var(--od-muted-4)"
                      : "var(--od-faint-2)",
                  fontWeight: on ? 500 : 400,
                }}
              >
                {index + 1}. {t[entry.label]}
              </button>
            );
          })}
        </div>

        <div className="p-[22px_24px_6px]">
          {id === "basics" ? (
            <div className="flex flex-col gap-[18px]">
              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_name}
                </label>
                <input
                  placeholder={t.field_name_placeholder}
                  className="border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-full rounded-lg border p-[10px_13px] text-[15px] outline-none"
                />
              </div>
              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_assistant}
                </label>
                <div className="flex flex-col gap-2">
                  {ASSISTANTS.map((entry, index) => (
                    <Option
                      key={entry.name}
                      on={assistant === index}
                      onClick={() => setAssistant(index)}
                    >
                      <span className="min-w-0 text-start">
                        <span className="text-od-text block font-medium">{entry.name}</span>
                        <span className="text-od-muted-5 mt-[2px] block text-[12.5px]">
                          {t[entry.note]}
                        </span>
                      </span>
                    </Option>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_caller_id}
                </label>
                <div className="border-od-border-6 bg-od-panel-deep-3 flex items-center gap-[10px] rounded-lg border p-[10px_13px]">
                  <span dir="ltr" className="mono ltr-data text-od-text-3">
                    {CALLER_ID}
                  </span>
                  <span className="text-od-faint text-[12.5px]">{t.caller_id_note}</span>
                </div>
              </div>
            </div>
          ) : null}

          {id === "targets" ? (
            <div className="flex flex-col gap-[18px]">
              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_targets}
                </label>
                <div className="flex flex-col gap-2">
                  {TARGETS.map((entry, index) => (
                    <Option key={entry.name} on={target === index} onClick={() => setTarget(index)}>
                      <span className="min-w-0 flex-[1_1_auto] text-start">
                        <span className="text-od-text block font-medium">{t[entry.name]}</span>
                        <span className="text-od-muted-5 mt-[2px] block text-[12.5px]">
                          {t[entry.note]}
                        </span>
                      </span>
                      <span dir="ltr" className="mono ltr-data text-od-text-3 flex-none text-[13px]">
                        {entry.count}
                      </span>
                    </Option>
                  ))}
                </div>
              </div>

              {/* Who is skipped and why — stated before the campaign starts, not after. */}
              <div className="border-od-border-4 bg-od-panel-deep-4 flex items-start gap-3 rounded-[9px] border p-[13px_15px]">
                <span className="text-od-faint mt-px flex-none">⌾</span>
                <div className="min-w-0">
                  <div className="text-od-text-3 font-medium">{t.skip_title}</div>
                  <div className="text-od-muted-5 mt-[3px] text-[13px] text-pretty">
                    {t.skip_body}
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {id === "script" ? (
            <div className="flex flex-col gap-[18px]">
              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_opening}
                </label>
                {/* The opening is spoken to the caller, so it stays in the caller's language. */}
                <textarea
                  rows={5}
                  dir="ltr"
                  defaultValue={t.opening_default}
                  className="border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-full resize-y rounded-lg border p-[12px_13px] text-start text-[14.5px] leading-[1.55] outline-none"
                />
                <div className="mt-[9px] flex flex-wrap gap-[7px]">
                  {TOKENS.map((token) => (
                    <button
                      key={token}
                      type="button"
                      dir="ltr"
                      className="mono border-od-border-7 text-od-muted-3 hover:text-od-text-2 hover:border-od-stroke cursor-pointer rounded-[5px] border bg-[var(--od-raise-3)] p-[4px_9px] text-[11.5px]"
                    >
                      {token}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_goal}
                </label>
                <div className="flex flex-col gap-2">
                  {GOALS.map((entry, index) => (
                    <Option key={entry} on={goal === index} onClick={() => setGoal(index)}>
                      <span className="text-od-text-3 min-w-0 text-start">{t[entry]}</span>
                    </Option>
                  ))}
                </div>
              </div>

              {/* Not a setting. The disclosure cannot be removed. */}
              <div className="border-od-red-border-4 bg-od-red-bg-6 flex items-start gap-3 rounded-[9px] border p-[13px_15px]">
                <span className="mt-px flex-none text-[color:var(--od-red-text-4)]">!</span>
                <div className="min-w-0">
                  <div className="font-medium text-[color:var(--od-red-text-8)]">
                    {t.disclosure_title}
                  </div>
                  <div className="mt-[3px] text-[13px] text-pretty text-[color:var(--od-red-text-7)]">
                    {t.disclosure_body}
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {id === "schedule" ? (
            <div className="flex flex-col gap-[18px]">
              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_window}
                </label>
                <div className="flex flex-wrap gap-[10px]">
                  {[
                    { label: t.window_from, value: "09:00" },
                    { label: t.window_until, value: "16:30" },
                  ].map((entry) => (
                    <div
                      key={entry.label}
                      className="border-od-border-6 bg-od-panel-deep-3 flex flex-[1_1_150px] items-center gap-[10px] rounded-lg border p-[10px_13px]"
                    >
                      <span className="text-od-faint text-[12px]">{entry.label}</span>
                      <span dir="ltr" className="mono ltr-data text-od-text-3">
                        {entry.value}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="mt-[10px] flex flex-wrap gap-[6px]">
                  {DAYS.map((label, index) => {
                    const on = days.includes(index + 1);
                    return (
                      <button
                        key={label}
                        type="button"
                        onClick={() =>
                          setDays((current) =>
                            on
                              ? current.filter((entry) => entry !== index + 1)
                              : [...current, index + 1],
                          )
                        }
                        className="min-w-[44px] cursor-pointer rounded-[7px] border p-[8px_10px] text-[13px]"
                        style={{
                          borderColor: on ? "var(--od-violet-border)" : "var(--od-border-4)",
                          background: on ? "var(--od-canvas-violet)" : "var(--od-panel-deep-3)",
                          color: on ? "var(--od-text)" : "var(--od-faint)",
                        }}
                      >
                        {t[label]}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_pace}
                </label>
                <div className="flex flex-wrap gap-2">
                  {PACE.map((entry, index) => (
                    <button
                      key={entry.name}
                      type="button"
                      onClick={() => setPace(index)}
                      className="flex-[1_1_150px] cursor-pointer rounded-[9px] border p-[12px_14px] text-start"
                      style={{
                        borderColor: pace === index ? "var(--od-violet-border)" : "var(--od-border-4)",
                        background:
                          pace === index ? "var(--od-canvas-violet)" : "var(--od-panel-deep-3)",
                      }}
                    >
                      <span className="text-od-text block font-medium">{t[entry.name]}</span>
                      <span className="text-od-muted-5 mt-[2px] block text-[12.5px]">
                        {t[entry.note]}
                      </span>
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
                  {t.field_retry}
                </label>
                <div className="border-od-border-6 bg-od-panel-deep-3 flex items-center justify-between gap-[14px] rounded-lg border p-[12px_14px]">
                  <span className="text-od-text-3">{t.retry_body}</span>
                  <span dir="ltr" className="mono ltr-data text-od-faint text-[12.5px]">
                    2 × 4h
                  </span>
                </div>
              </div>

              <div className="border-od-border-4 bg-od-panel-deep-4 rounded-[9px] border p-[14px_16px]">
                <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
                  {t.estimate_label}
                </div>
                <div className="text-od-text-3 mt-[7px] text-pretty">
                  {t.estimate_body_before}
                  <span className="mono">{t.estimate_days}</span>
                  {t.estimate_body_middle}
                  <span className="mono">{t.estimate_cost}</span>
                  {t.estimate_body_after}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <div className="border-od-border bg-od-panel-deep-2 mt-[18px] flex flex-wrap items-center justify-between gap-[10px] border-t p-[16px_24px]">
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_15px]"
          >
            {t.cancel}
          </button>
          <div className="flex flex-wrap gap-[9px]">
            {step > 0 ? (
              <button
                type="button"
                onClick={() => setStep(step - 1)}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_15px] font-medium"
              >
                {t.back}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => (step === 3 ? onClose() : setStep(step + 1))}
              className="cursor-pointer rounded-md border border-[color:var(--od-violet-deep)] bg-[color:var(--od-violet-solid)] p-[9px_17px] font-medium text-white hover:bg-[color:var(--od-violet-deep)]"
            >
              {step === 3 ? t.review_and_start : t.continue}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConsentLogFull({ locale, t }: { locale: Locale; t: CampaignsDictionary }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.error_label}
        </div>
        <h2 className="text-od-text mt-[18px] mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        {/* Failing closed: it would rather stop than call anyone unrecorded. */}
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
          <span>consent-log/enospc</span>
          <span>/var/telagent/consent</span>
          <span>2026-08-20 09:41:07</span>
        </div>
      </div>
    </div>
  );
}
