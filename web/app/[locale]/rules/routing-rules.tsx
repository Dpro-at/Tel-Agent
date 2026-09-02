"use client";

import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  addRule,
  allSettings,
  ApiError,
  changeRule,
  removeRule,
  rulesList,
  saveSettings,
  type RoutingRule,
  type RuleAction,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { RulesDictionary } from "./page";

type Key = keyof RulesDictionary;

/** The three columns, §A6.5: always through · blocked · AI handles. */
const COLUMNS: {
  id: RuleAction;
  label: Key;
  note: Key;
  color: string;
  background: string;
  border: string;
  dot: string;
}[] = [
  {
    id: "pass",
    label: "column_pass",
    note: "column_pass_note",
    color: "var(--od-green-text)",
    background: "rgba(63,185,132,.11)",
    border: "var(--od-green-border)",
    dot: "var(--od-green)",
  },
  {
    id: "block",
    label: "column_block",
    note: "column_block_note",
    color: "var(--od-red-text-4)",
    background: "rgba(240,96,94,.11)",
    border: "var(--od-red-border)",
    dot: "#F0605E",
  },
  {
    id: "ai",
    label: "column_ai",
    note: "column_ai_note",
    color: "var(--od-violet-3)",
    background: "rgba(139,124,255,.13)",
    border: "var(--od-violet-border)",
    dot: "var(--od-violet)",
  },
];

const MOVE_LABEL: Record<RuleAction, Key> = {
  pass: "move_to_pass",
  block: "move_to_block",
  ai: "move_to_ai",
};

export function RoutingRules({ locale, t }: { locale: Locale; t: RulesDictionary }) {
  const list = useResource<RoutingRule[]>(() => rulesList(), []);

  const [adding, setAdding] = useState(false);
  const [pattern, setPattern] = useState("");
  const [action, setAction] = useState<RuleAction>("block");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<number | "add" | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const rules = list.data ?? [];

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "invalid_pattern") return t.error_invalid_pattern;
      if (thrown.code === "rule_exists") return t.error_rule_exists;
      return thrown.message;
    }
    return String(thrown);
  };

  const submit = async () => {
    setBusy("add");
    setNotice(null);
    try {
      await addRule(pattern.trim(), action, note.trim() || undefined);
      setPattern("");
      setNote("");
      setAdding(false);
      list.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(null);
    }
  };

  const move = async (rule: RoutingRule, to: RuleAction) => {
    setBusy(rule.id);
    setNotice(null);
    try {
      await changeRule(rule.id, to, rule.note);
      list.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (rule: RoutingRule) => {
    setBusy(rule.id);
    setNotice(null);
    try {
      await removeRule(rule.id);
      setConfirming(null);
      list.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="settings" />
      </div>

      <div className="mx-auto max-w-[1400px] p-[26px_28px_80px]">
        <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
          <div className="max-w-[70ch]">
            <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
              {t.title}
            </h1>
            <p className="text-od-muted-4 mt-[6px] text-pretty">{t.subtitle}</p>
          </div>
          <button
            type="button"
            onClick={() => setAdding((value) => !value)}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 flex-none cursor-pointer rounded-[7px] border p-[9px_15px] font-medium"
          >
            {t.add_rule}
          </button>
        </div>

        {adding ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
            className="border-od-line bg-od-panel-deep-3 mt-[18px] rounded-[10px] border p-[16px_18px]"
          >
            <div className="flex flex-wrap items-end gap-[14px]">
              <label className="flex min-w-[200px] flex-[1_1_220px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_pattern}
                </span>
                <input
                  value={pattern}
                  onChange={(event) => setPattern(event.target.value)}
                  placeholder="+43 664 123456 · boss@example.com · @username"
                  dir="ltr"
                  required
                  maxLength={320}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono ltr-data rounded-lg border p-[9px_13px] text-start outline-none"
                />
              </label>
              <div className="flex flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_action}
                </span>
                <div className="flex gap-2">
                  {COLUMNS.map((column) => (
                    <button
                      key={column.id}
                      type="button"
                      onClick={() => setAction(column.id)}
                      className="cursor-pointer rounded-[7px] border p-[8px_12px] text-[13px] whitespace-nowrap"
                      style={
                        action === column.id
                          ? {
                              borderColor: column.border,
                              background: column.background,
                              color: column.color,
                              fontWeight: 600,
                            }
                          : {
                              borderColor: "var(--od-border-7)",
                              background: "var(--od-panel-deep-3)",
                              color: "var(--od-muted-4)",
                            }
                      }
                    >
                      {t[column.label]}
                    </button>
                  ))}
                </div>
              </div>
              <label className="flex min-w-[180px] flex-[1_1_220px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_note}
                </span>
                <input
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder={t.field_note_hint}
                  maxLength={200}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-lg border p-[9px_13px] outline-none"
                />
              </label>
              <button
                type="submit"
                disabled={busy === "add"}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-lg border p-[9px_15px] font-medium disabled:cursor-default disabled:opacity-50"
              >
                {t.add_confirm}
              </button>
              <button
                type="button"
                onClick={() => {
                  setAdding(false);
                  setNotice(null);
                }}
                className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-lg border bg-transparent p-[9px_15px]"
              >
                {t.add_cancel}
              </button>
            </div>
            <p className="text-od-faint m-0 mt-[10px] max-w-[76ch] text-[12.5px] text-pretty">
              {t.add_note}
            </p>
          </form>
        ) : null}

        {notice ? (
          <div className="border-od-red-border-3 bg-od-red-bg-4 mt-[14px] rounded-[9px] border p-[10px_14px] text-[13px] text-[color:var(--od-red-text-6)]">
            {notice}
          </div>
        ) : null}

        {list.loading && list.data === null ? <RulesSkeleton /> : null}

        {list.error !== null && list.data === null ? (
          <div className="border-od-red-border-3 bg-od-red-bg-4 mt-5 rounded-[10px] border p-[18px_20px]">
            <h3 className="m-0 text-[16px] font-semibold text-[color:var(--od-red-text-3)]">
              {list.error.kind === "offline" ? t.error_offline_title : t.error_failed_title}
            </h3>
            <p className="mt-[6px] max-w-[62ch] text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
              {list.error.message}
            </p>
            <button
              type="button"
              onClick={list.reload}
              className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
            >
              {t.retry}
            </button>
          </div>
        ) : null}

        {list.data !== null ? (
          <div className="mt-[18px] flex flex-wrap items-start gap-5">
            {COLUMNS.map((column) => {
              const entries = rules.filter((rule) => rule.action === column.id);
              return (
                <section
                  key={column.id}
                  className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,300px)] flex-[1_1_320px] overflow-hidden rounded-[10px] border"
                >
                  <header className="bg-od-canvas-2 border-b border-[color:var(--od-raise-6)] p-[12px_16px]">
                    <div className="flex items-center gap-[8px]">
                      <span
                        className="size-[8px] flex-none rounded-full"
                        style={{ background: column.dot }}
                      />
                      <span className="text-od-text text-[14px] font-semibold">
                        {t[column.label]}
                      </span>
                      <span className="text-od-faint-2 ms-auto text-[11.5px]">
                        {entries.length === 1
                          ? t.rules_one
                          : interpolate(t.rules_many, { count: entries.length })}
                      </span>
                    </div>
                    <p className="text-od-muted-5 m-0 mt-[4px] text-[12px] text-pretty">
                      {t[column.note]}
                    </p>
                  </header>

                  {entries.length === 0 ? (
                    <div className="text-od-faint p-[18px_16px] text-[13px]">
                      {t.column_empty}
                    </div>
                  ) : (
                    entries.map((rule) => {
                      const rowBusy = busy === rule.id;
                      const others = COLUMNS.filter((other) => other.id !== column.id);
                      return (
                        <div
                          key={rule.id}
                          className="border-b border-[color:var(--od-raise-6)] p-[12px_16px]"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span
                              dir="ltr"
                              className="mono ltr-data text-od-text text-start text-[13.5px] font-medium"
                            >
                              {rule.pattern}
                            </span>
                          </div>
                          {rule.note ? (
                            <div className="text-od-muted-2 mt-[4px] text-[12.5px] text-pretty">
                              {rule.note}
                            </div>
                          ) : null}
                          {/* Rule and consequence in one view (§A6.5): what the
                              archive last saw from this pattern. */}
                          <div className="text-od-faint mt-[5px] text-[12px]">
                            {rule.last_called_at
                              ? interpolate(t.last_called, {
                                  when: new Date(rule.last_called_at).toLocaleDateString(
                                    locale,
                                    { day: "numeric", month: "short" },
                                  ),
                                  time: new Date(rule.last_called_at).toLocaleTimeString(
                                    locale,
                                    { hour: "2-digit", minute: "2-digit" },
                                  ),
                                })
                              : t.never_called}
                          </div>
                          <div className="mt-[8px] flex flex-wrap gap-[6px]">
                            {others.map((other) => (
                              <button
                                key={other.id}
                                type="button"
                                disabled={rowBusy}
                                onClick={() => void move(rule, other.id)}
                                className="border-od-border-7 text-od-muted-4 hover:text-od-text-3 cursor-pointer rounded-md border bg-transparent p-[4px_10px] text-[12px] hover:bg-[var(--od-raise-4)] disabled:cursor-default disabled:opacity-50"
                              >
                                {t[MOVE_LABEL[other.id]]}
                              </button>
                            ))}
                            {confirming === rule.id ? (
                              <>
                                <button
                                  type="button"
                                  disabled={rowBusy}
                                  onClick={() => void remove(rule)}
                                  className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[4px_10px] text-[12px] font-medium text-[color:var(--od-red-text-3)] disabled:cursor-default disabled:opacity-50"
                                >
                                  {t.remove_confirm}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => setConfirming(null)}
                                  className="border-od-border-2 text-od-muted cursor-pointer rounded-md border bg-transparent p-[4px_10px] text-[12px]"
                                >
                                  {t.remove_cancel}
                                </button>
                              </>
                            ) : (
                              <button
                                type="button"
                                disabled={rowBusy}
                                onClick={() => setConfirming(rule.id)}
                                className="border-od-border-7 cursor-pointer rounded-md border bg-transparent p-[4px_10px] text-[12px] text-[color:var(--od-red-text-4)] hover:bg-[var(--od-raise-4)] disabled:cursor-default disabled:opacity-50"
                              >
                                {t.remove}
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })
                  )}
                </section>
              );
            })}
          </div>
        ) : null}

        <BusinessHours t={t} />
      </div>
    </div>
  );
}

/**
 * §A6.5's other item on this screen: business hours. Outside them the agent always
 * answers — a `pass` rule holds only while somebody is at the desk to pass to.
 * Two workspace settings behind `/api/settings`; `block` ignores the clock.
 */
function BusinessHours({ t }: { t: RulesDictionary }) {
  const stored = useResource(() => allSettings(), []);
  const [hours, setHours] = useState<string | null>(null);
  const [timezone, setTimezone] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [state, setState] = useState<"idle" | "saved" | "failed">("idle");

  const current = (key: string): string => {
    const row = stored.data?.find((entry) => entry.key === key);
    return row?.value === null || row?.value === undefined ? "" : String(row.value);
  };
  const shownHours = hours ?? current("routing.hours");
  const shownTimezone = timezone ?? current("routing.timezone");

  const save = async () => {
    setSaving(true);
    setState("idle");
    try {
      await saveSettings({
        "routing.hours": shownHours.trim(),
        "routing.timezone": shownTimezone.trim(),
      });
      setState("saved");
    } catch {
      setState("failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="border-od-line bg-od-panel-deep-3 mt-5 max-w-[720px] rounded-[10px] border p-[16px_18px]">
      <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
        {t.hours_title}
      </h3>
      <p className="text-od-faint mt-[6px] mb-0 max-w-[70ch] text-[12.5px] text-pretty">
        {t.hours_note}
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-[14px]">
        <label className="flex min-w-[200px] flex-[1_1_220px] flex-col gap-[6px]">
          <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
            {t.hours_field}
          </span>
          <input
            value={shownHours}
            onChange={(event) => setHours(event.target.value)}
            placeholder="mo-fr 08:00-18:00"
            dir="ltr"
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono ltr-data rounded-lg border p-[9px_13px] text-start outline-none"
          />
        </label>
        <label className="flex min-w-[180px] flex-[1_1_200px] flex-col gap-[6px]">
          <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
            {t.hours_timezone}
          </span>
          <input
            value={shownTimezone}
            onChange={(event) => setTimezone(event.target.value)}
            placeholder="Europe/Vienna"
            dir="ltr"
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono ltr-data rounded-lg border p-[9px_13px] text-start outline-none"
          />
        </label>
        <button
          type="button"
          disabled={saving || stored.data === null}
          onClick={() => void save()}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-lg border p-[9px_15px] font-medium disabled:cursor-default disabled:opacity-50"
        >
          {t.hours_save}
        </button>
      </div>
      {state === "saved" ? (
        <p className="m-0 mt-[8px] text-[12.5px] text-[color:var(--od-green-text)]">
          {t.hours_saved}
        </p>
      ) : null}
      {state === "failed" ? (
        <p className="m-0 mt-[8px] text-[12.5px] text-[color:var(--od-red-text-6)]">
          {t.hours_failed}
        </p>
      ) : null}
    </section>
  );
}

function RulesSkeleton() {
  const shimmer = {
    background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  };

  return (
    <div className="mt-[18px] flex flex-wrap gap-5">
      {[0, 1, 2].map((index) => (
        <div
          key={index}
          className="border-od-raise-12 h-[280px] min-w-[min(100%,300px)] flex-[1_1_320px] rounded-[10px] border"
          style={shimmer}
        />
      ))}
    </div>
  );
}
