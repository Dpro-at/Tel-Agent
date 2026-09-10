"use client";

import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { RulesDictionary } from "./page";

type Key = keyof RulesDictionary;

/** What a matched rule does. Three outcomes, and only three. */
const ACTIONS = {
  human: {
    label: "action_human" as Key,
    color: "var(--od-green-text)",
    background: "rgba(63,185,132,.11)",
    border: "var(--od-green-border)",
    dot: "var(--od-green)",
    note: "action_human_note" as Key,
  },
  blocked: {
    label: "action_blocked" as Key,
    color: "var(--od-red-text-4)",
    background: "rgba(240,96,94,.11)",
    border: "var(--od-red-border)",
    dot: "#F0605E",
    note: "action_blocked_note" as Key,
  },
  agent: {
    label: "action_agent" as Key,
    color: "var(--od-violet-3)",
    background: "rgba(139,124,255,.13)",
    border: "var(--od-violet-border)",
    dot: "var(--od-violet)",
    note: "action_agent_note" as Key,
  },
} as const;

type ActionId = keyof typeof ACTIONS;

/**
 * A match pattern is machine syntax and stays verbatim - except where it is written
 * in words, like a weekday range, which reads as a sentence and so carries a key.
 */
const RULES: {
  name: Key;
  match?: string;
  matchKey?: Key;
  action: ActionId;
  hits: number;
  note?: Key;
  off?: boolean;
}[] = [
  {
    name: "rule_spam",
    matchKey: "rule_spam_match",
    action: "blocked",
    hits: 61,
    note: "rule_spam_note",
  },
  { name: "rule_withheld", match: "caller_id: anonymous", action: "blocked", hits: 9 },
  {
    name: "rule_staff",
    match: "+43 1 512 3390, +43 664 900 1120",
    action: "human",
    hits: 34,
  },
  {
    name: "rule_emergency",
    matchKey: "rule_emergency_match",
    action: "human",
    hits: 0,
    note: "rule_emergency_note",
  },
  {
    name: "rule_reception",
    matchKey: "rule_reception_match",
    action: "human",
    hits: 212,
    note: "rule_reception_note",
  },
  {
    name: "rule_customers",
    matchKey: "rule_customers_match",
    action: "agent",
    hits: 188,
  },
  {
    name: "rule_afterhours",
    matchKey: "rule_afterhours_match",
    action: "agent",
    hits: 97,
    note: "rule_afterhours_note",
  },
  {
    name: "rule_prefix",
    match: "+43 720*",
    action: "blocked",
    hits: 3,
    off: true,
    note: "rule_prefix_note",
  },
];

const TRACE: { ok: boolean; label: Key }[] = [
  { ok: false, label: "trace_1" },
  { ok: false, label: "trace_2" },
  { ok: false, label: "trace_3" },
  { ok: false, label: "trace_4" },
  { ok: true, label: "trace_5" },
];

const CONDITIONS = {
  number: {
    label: "cond_number" as Key,
    placeholder: "+43 1 512 3390",
    hint: "cond_number_hint" as Key,
    say: "cond_number_say" as Key,
  },
  prefix: {
    label: "cond_prefix" as Key,
    placeholder: "+43 720*",
    hint: "cond_prefix_hint" as Key,
    say: "cond_prefix_say" as Key,
  },
  group: {
    label: "cond_group" as Key,
    placeholder: "group: customers",
    hint: "cond_group_hint" as Key,
    say: "cond_group_say" as Key,
  },
  hours: {
    label: "cond_hours" as Key,
    placeholder: "Mon–Fri 08:00–12:00",
    hint: "cond_hours_hint" as Key,
    say: "cond_hours_say" as Key,
  },
  anon: {
    label: "cond_anon" as Key,
    placeholder: "caller_id: anonymous",
    hint: "cond_anon_hint" as Key,
    say: "cond_anon_say" as Key,
  },
} as const;

type ConditionId = keyof typeof CONDITIONS;

const ACTION_CHOICES: { id: ActionId; label: Key; note: Key }[] = [
  { id: "human", label: "choice_human", note: "choice_human_note" },
  { id: "blocked", label: "choice_blocked", note: "choice_blocked_note" },
  { id: "agent", label: "choice_agent", note: "choice_agent_note" },
];

const POSITIONS: Record<PositionId, { chip: Key; say: Key }> = {
  top: { chip: "pos_top", say: "pos_top_say" },
  after5: { chip: "pos_after5", say: "pos_after5_say" },
  bottom: { chip: "pos_bottom", say: "pos_bottom_say" },
};

type PositionId = "top" | "after5" | "bottom";

/** Assistant names are proper names in every language. */
const ASSISTANT_NAMES = ["Rezeption Wagner", "Nachbetreuung", "Reception EN"];

function Chip({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`cursor-pointer rounded-[7px] border p-[7px_12px] text-[13px] whitespace-nowrap ${
        on
          ? "border-od-stroke bg-od-line-2 text-od-text"
          : "border-od-border-7 bg-od-panel-deep-3 text-od-muted-4"
      }`}
    >
      {label}
    </button>
  );
}

export function RoutingRules({ locale, t }: { locale: Locale; t: RulesDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [newOpen, setNewOpen] = useState(false);
  const [testNumber, setTestNumber] = useState("+43 664 1234567");

  const offline = state === "offline";
  const empty = state === "empty";
  const showRules = state === "default" || empty || offline;

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="settings" />
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
            <div className="mt-[3px] text-[color:var(--od-red-text-2)]">
              {t.offline_body_before}
              <span className="mono">sip.easybell.de</span>
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

      <div className="mx-auto max-w-[1400px] p-[26px_28px_80px]">
        {state === "error" ? <InvalidRulesFile t={t} /> : null}
        {state === "loading" ? <RulesSkeleton /> : null}

        {showRules ? (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div className="max-w-[62ch]">
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
              </div>
              <button
                type="button"
                onClick={() => setNewOpen(true)}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_15px] font-medium"
              >
                {t.new_rule}
              </button>
            </div>

            {!empty ? (
              <div
                className="border-od-line mt-5 grid gap-px overflow-hidden rounded-[10px] border"
                style={{
                  gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                  background: "var(--od-line)",
                }}
              >
                {(Object.keys(ACTIONS) as ActionId[]).map((id) => (
                  <div key={id} className="bg-od-panel-deep-3 p-[13px_16px]">
                    <div className="flex items-center gap-2">
                      <span
                        className="size-2 flex-none rounded-full"
                        style={{ background: ACTIONS[id].dot }}
                      />
                      <span className="text-od-text-3 text-[12.5px] font-medium">
                        {t[ACTIONS[id].label]}
                      </span>
                    </div>
                    <div className="text-od-muted-5 mt-[5px] text-[12.5px] text-pretty">
                      {t[ACTIONS[id].note]}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="mt-5 flex flex-wrap items-start gap-[18px]">
              <div className="min-w-[min(100%,460px)] flex-[3_1_520px]">
                {empty ? (
                  <div className="border-od-border-6 bg-od-panel-deep-2 rounded-[10px] border border-dashed p-[44px_28px] text-center">
                    <h3 className="text-od-text m-0 text-[18px] font-semibold">{t.empty_title}</h3>
                    <p className="text-od-muted mx-auto mt-[10px] max-w-[56ch] text-pretty">
                      {t.empty_body}
                    </p>
                    <button
                      type="button"
                      onClick={() => setNewOpen(true)}
                      className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-[18px] cursor-pointer rounded-md border p-[9px_16px] font-medium"
                    >
                      {t.empty_cta}
                    </button>
                  </div>
                ) : (
                  <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
                    {RULES.map((rule, index) => {
                      const action = ACTIONS[rule.action];
                      return (
                        <div
                          key={rule.name}
                          className="hover:bg-od-raise flex cursor-pointer flex-wrap items-center gap-x-[14px] gap-y-[10px] border-b border-[color:var(--od-raise-6)] p-[13px_16px]"
                          style={{ opacity: rule.off ? 0.55 : 1 }}
                        >
                          <span
                            dir="ltr"
                            className="mono ltr-data text-od-faint-2 w-[26px] flex-none text-[12.5px]"
                          >
                            {index + 1}
                          </span>
                          <span
                            className="flex-none cursor-grab text-[13px] text-[color:var(--od-faint-5)]"
                            title={t.drag}
                          >
                            ⋮⋮
                          </span>
                          <div className="min-w-0 flex-[1_1_240px]">
                            <div className="flex flex-wrap items-center gap-x-[10px] gap-y-[7px]">
                              <span className="text-od-text font-medium text-pretty">
                                {t[rule.name]}
                              </span>
                              {rule.off ? (
                                <span className="border-od-border-3 text-od-faint rounded border bg-transparent p-[1px_7px] text-[11px] font-semibold">
                                  {t.off}
                                </span>
                              ) : null}
                            </div>
                            <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-[5px]">
                              <span className="text-od-faint-2 text-[12px]">{t.when}</span>
                              {/* A pattern is machine syntax; a weekday range is a phrase. */}
                              {rule.matchKey ? (
                                <span className="text-od-muted-2 text-[12.5px] [overflow-wrap:anywhere]">
                                  {t[rule.matchKey]}
                                </span>
                              ) : (
                                <span
                                  dir="ltr"
                                  className="mono ltr-data text-od-muted-2 text-start text-[12.5px] [overflow-wrap:anywhere]"
                                >
                                  {rule.match}
                                </span>
                              )}
                            </div>
                            {rule.note ? (
                              <div className="text-od-muted-5 mt-[5px] text-[12.5px] text-pretty">
                                {t[rule.note]}
                              </div>
                            ) : null}
                          </div>
                          <span
                            className="flex-none rounded-md border p-[3px_10px] text-[12.5px] font-medium whitespace-nowrap"
                            style={{
                              borderColor: action.border,
                              background: action.background,
                              color: action.color,
                            }}
                          >
                            {t[action.label]}
                          </span>
                          <span className="text-od-faint-2 min-w-[96px] flex-none text-end text-[11.5px]">
                            {interpolate(t.hits, { count: rule.hits })}
                          </span>
                        </div>
                      );
                    })}

                    {/* The fallback is what guarantees no call is left unanswered. */}
                    <div className="bg-od-canvas-2 flex flex-wrap items-center gap-x-[14px] gap-y-[10px] p-[14px_16px]">
                      <span
                        dir="ltr"
                        className="mono ltr-data text-od-faint-2 w-[26px] flex-none text-[11.5px]"
                      >
                        {t.fallback_last}
                      </span>
                      <div className="min-w-0 flex-[1_1_240px]">
                        <div className="text-od-text-5 font-medium">{t.fallback_title}</div>
                        <div className="text-od-muted-5 mt-[3px] text-[12.5px] text-pretty">
                          {t.fallback_note}
                        </div>
                      </div>
                      <span className="flex-none rounded-md border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.13)] p-[3px_10px] text-[12.5px] font-medium whitespace-nowrap text-[color:var(--od-violet-3)]">
                        Rezeption Wagner
                      </span>
                      <span className="text-od-faint-2 min-w-[96px] flex-none text-end text-[11.5px]">
                        {interpolate(t.hits, { count: 142 })}
                      </span>
                    </div>
                  </div>
                )}
              </div>

              {/* Replays the list against a number without dialling anything. */}
              <div className="border-od-line bg-od-panel-deep-3 max-w-[380px] min-w-[min(100%,290px)] flex-[1_1_300px] rounded-[10px] border p-[18px]">
                <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                  {t.try_heading}
                </div>

                {empty ? (
                  <div className="mt-[10px] text-[13.5px] text-pretty text-[color:var(--od-muted-3)]">
                    {t.try_empty}
                  </div>
                ) : (
                  <>
                    <input
                      value={testNumber}
                      onChange={(event) => setTestNumber(event.target.value)}
                      placeholder={t.try_placeholder}
                      dir="ltr"
                      className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[10px] w-full rounded-lg border p-[10px_12px] text-[14px] outline-none"
                    />
                    <div className="mt-3 flex flex-col gap-[6px]">
                      {TRACE.map((step) => (
                        <div
                          key={step.label}
                          className="bg-od-canvas-2 flex items-baseline gap-[9px] rounded-[7px] p-[8px_10px]"
                        >
                          <span
                            className="w-4 flex-none text-[12px] font-semibold"
                            style={{
                              color: step.ok ? "var(--od-green-text)" : "var(--od-faint-2)",
                            }}
                          >
                            {step.ok ? "✓" : "✕"}
                          </span>
                          <span className="text-od-text-4 min-w-0 flex-[1_1_auto] text-[13px] text-pretty">
                            {t[step.label]}
                          </span>
                        </div>
                      ))}
                    </div>
                    <div className="mt-3 rounded-lg border border-[color:var(--od-violet-border)] bg-[var(--od-canvas-violet)] p-[12px_13px]">
                      <div className="text-[12px] text-[color:var(--od-violet-2)]">{t.result}</div>
                      <div className="text-od-text-3 mt-1 text-[14px] text-pretty">{t.result_body}</div>
                    </div>
                    <div className="text-od-faint mt-[11px] text-[12.5px] text-pretty">
                      {t.try_footer}
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      {newOpen ? <NewRuleDialog t={t} onClose={() => setNewOpen(false)} /> : null}
    </div>
  );
}

function NewRuleDialog({ t, onClose }: { t: RulesDictionary; onClose: () => void }) {
  const [name, setName] = useState("");
  const [condition, setCondition] = useState<ConditionId>("number");
  const [value, setValue] = useState("");
  const [action, setAction] = useState<ActionId>("human");
  const [assistant, setAssistant] = useState(ASSISTANT_NAMES[0]);
  const [position, setPosition] = useState<PositionId>("top");

  // The sentence is assembled from three translated fragments rather than
  // concatenated in English, so each language can order them its own way.
  const plain = interpolate(t.plain_line, {
    condition: t[CONDITIONS[condition].say],
    outcome:
      action === "human"
        ? t.plain_human
        : action === "blocked"
          ? t.plain_blocked
          : interpolate(t.plain_agent, { assistant }),
    position: t[POSITIONS[position].say],
  });

  // A rule that can never be reached, or one wide enough to catch customers.
  const shadowed = position === "bottom" || condition === "prefix";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center overflow-auto p-[40px_20px]"
      style={{ background: "var(--od-scrim)" }}
    >
      <div
        className="border-od-border-9 bg-od-panel w-full max-w-[680px] overflow-hidden rounded-[14px] border"
        style={{ boxShadow: "0 26px 70px var(--od-scrim-3)" }}
      >
        <div className="border-od-border flex items-start justify-between gap-4 border-b p-[20px_24px_16px]">
          <div>
            <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.dialog_title}</h2>
            <div className="text-od-muted-4 mt-1 text-[13px]">{t.dialog_subtitle}</div>
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

        <div className="p-[20px_24px]">
          <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
            {t.form_name}
          </label>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t.form_name_placeholder}
            className="border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-full rounded-lg border p-[10px_13px] text-[15px] outline-none"
          />

          <div className="mt-5">
            <div className="text-od-text-5 mb-[7px] text-[12.5px] font-medium">{t.form_when}</div>
            <div className="flex flex-wrap gap-[7px]">
              {(Object.keys(CONDITIONS) as ConditionId[]).map((id) => (
                <Chip
                  key={id}
                  label={t[CONDITIONS[id].label]}
                  on={condition === id}
                  onClick={() => setCondition(id)}
                />
              ))}
            </div>
            <input
              value={value}
              onChange={(event) => setValue(event.target.value)}
              placeholder={CONDITIONS[condition].placeholder}
              dir="ltr"
              className="mono ltr-data border-od-border-6 bg-od-panel-deep-3 text-od-text-2 mt-[10px] w-full rounded-lg border p-[10px_13px] text-[14px] outline-none"
            />
            <div className="text-od-faint mt-[6px] text-[12.5px] text-pretty">
              {CONDITIONS[condition].hint}
            </div>
          </div>

          <div className="mt-5">
            <div className="text-od-text-5 mb-[7px] text-[12.5px] font-medium">{t.form_then}</div>
            <div className="flex flex-col gap-2">
              {ACTION_CHOICES.map((choice) => {
                const on = action === choice.id;
                return (
                  <button
                    key={choice.id}
                    type="button"
                    onClick={() => setAction(choice.id)}
                    className="flex w-full cursor-pointer items-center gap-[11px] rounded-[9px] border p-[12px_14px] text-start"
                    style={{
                      borderColor: on ? "var(--od-violet-border)" : "var(--od-border-4)",
                      background: on ? "var(--od-canvas-violet)" : "var(--od-panel-deep-3)",
                    }}
                  >
                    <span
                      className="size-[15px] flex-none rounded-full"
                      style={{
                        border: on ? "4.5px solid var(--od-violet)" : "1.5px solid var(--od-stroke-3)",
                        background: on ? "var(--od-white)" : "transparent",
                      }}
                    />
                    <span className="min-w-0 text-start">
                      <span className="text-od-text block font-medium">{t[choice.label]}</span>
                      <span className="text-od-muted-5 mt-[2px] block text-[12.5px] text-pretty">
                        {choice.note}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {action === "agent" ? (
            <div className="mt-[14px]">
              <div className="text-od-text-5 mb-[7px] text-[12.5px] font-medium">{t.form_which}</div>
              <div className="flex flex-wrap gap-[7px]">
                {["Rezeption Wagner", "Nachbetreuung", "Reception EN"].map((label) => (
                  <Chip
                    key={label}
                    label={label}
                    on={assistant === label}
                    onClick={() => setAssistant(label)}
                  />
                ))}
              </div>
            </div>
          ) : null}

          <div className="mt-5">
            <div className="text-od-text-5 mb-[7px] text-[12.5px] font-medium">{t.form_where}</div>
            <div className="flex flex-wrap gap-[7px]">
              {(
                [
                  ["top", t.pos_top],
                  ["after5", t.pos_after5],
                  ["bottom", t.pos_bottom],
                ] as const
              ).map(([id, label]) => (
                <Chip key={id} label={label} on={position === id} onClick={() => setPosition(id)} />
              ))}
            </div>
          </div>

          {/* The rule read back as a sentence, so order is never a guess. */}
          <div className="mt-5 rounded-[9px] border border-[color:var(--od-violet-border)] bg-[var(--od-canvas-violet)] p-[14px_16px]">
            <div className="text-[11px] tracking-[.08em] uppercase text-[color:var(--od-violet-2)]">
              {t.plain_heading}
            </div>
            <div className="text-od-text-3 mt-[6px] text-[14.5px] text-pretty">{plain}</div>
          </div>

          {shadowed ? (
            <div className="border-od-amber-border-2 mt-3 flex items-start gap-[11px] rounded-[9px] border bg-[var(--od-amber-bg-2)] p-[13px_15px]">
              <span className="mt-px flex-none text-[color:var(--od-amber)]">!</span>
              <div className="min-w-0 text-[13px] text-pretty text-[color:var(--od-amber-text-3)]">
                {position === "bottom"
                  ? t.warn_bottom
                  : t.warn_prefix}
              </div>
            </div>
          ) : null}
        </div>

        <div className="border-od-border bg-od-panel-deep-2 flex flex-wrap justify-end gap-[10px] border-t p-[16px_24px]">
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px]"
          >
            {t.cancel}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 cursor-pointer rounded-[7px] border p-[9px_17px] font-semibold"
          >
            {t.add_rule}
          </button>
        </div>
      </div>
    </div>
  );
}

function InvalidRulesFile({ t }: { t: RulesDictionary }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.error_label}
        </div>
        <h2 className="mt-[18px] mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">
          {t.error_body_before}
          <span className="mono">routing.yaml</span>
          {t.error_body_after}
        </p>
        <div
          dir="ltr"
          className="border-od-border-2 bg-od-canvas-2 mono ltr-data text-od-text-5 mt-[18px] rounded-lg border p-[12px_14px] text-[12.5px]"
        >
          line 24: <span className="text-[color:var(--od-red-text-5)]">expected key “match”</span>
        </div>
        <div className="mt-5 flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            {t.error_open}
          </button>
          <button
            type="button"
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_16px]"
          >
            {t.error_restore}
          </button>
        </div>
      </div>
    </div>
  );
}

function RulesSkeleton() {
  const shimmer = {
    background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  };

  return (
    <div>
      <div
        className="h-7 w-[190px] rounded-md"
        style={{
          background: "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
          backgroundSize: "420px 100%",
          animation: "od-shimmer 1.4s linear infinite",
        }}
      />
      <div className="border-od-raise-12 mt-5 h-[62px] rounded-[10px] border" style={shimmer} />
      <div className="mt-5 flex flex-wrap items-start gap-[18px]">
        <div className="border-od-raise-12 bg-od-panel-deep-3 min-w-[min(100%,460px)] flex-[3_1_520px] overflow-hidden rounded-[10px] border">
          {[72, 58, 84, 66, 90, 62, 78, 54].map((width, index) => (
            <div
              key={index}
              className="flex items-center gap-[14px] border-b border-[color:var(--od-raise-6)] p-[15px_16px]"
            >
              <div className="h-3 w-[18px] flex-none rounded bg-[var(--od-raise-4)]" />
              <div
                className="h-[14px] flex-[1_1_auto] rounded"
                style={{
                  maxWidth: `${width}%`,
                  background:
                    "linear-gradient(90deg,var(--od-raise-2),var(--od-raise-11),var(--od-raise-2))",
                  backgroundSize: "420px 100%",
                  animation: "od-shimmer 1.4s linear infinite",
                }}
              />
              <div className="h-5 w-[110px] flex-none rounded-[5px] bg-[var(--od-raise-8)]" />
            </div>
          ))}
        </div>
        <div
          className="border-od-raise-12 h-[300px] max-w-[380px] min-w-[min(100%,290px)] flex-[1_1_300px] rounded-[10px] border"
          style={shimmer}
        />
      </div>
    </div>
  );
}
