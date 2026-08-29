"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import { EXTERNAL } from "@/lib/links";
import type { Locale } from "@/lib/locales";

import type { UpdateDictionary } from "./page";

/** Versions are data: the same string in every language. */
const CURRENT = "v1.4.2";
const NEXT = "v1.5.0";
const NEXT_SECURITY = "v1.4.3";

type Tone = "red" | "amber" | "green" | "grey";

const TONES: Record<Tone, { border: string; background: string; color: string }> = {
  red: { border: "var(--od-red-border-3)", background: "var(--od-red-bg-5)", color: "var(--od-red-text-5)" },
  amber: { border: "var(--od-amber-border)", background: "var(--od-amber-bg)", color: "var(--od-amber-text)" },
  green: { border: "var(--od-green-border)", background: "rgba(63,185,132,.10)", color: "var(--od-green-text)" },
  grey: { border: "var(--od-border-7)", background: "var(--od-raise-5)", color: "var(--od-muted-5)" },
};

type ChangeKey = keyof UpdateDictionary;

const CHANGES: { tag: ChangeKey; tone: Tone; title: ChangeKey; body: ChangeKey }[] = [
  { tag: "tag_breaking", tone: "red", title: "change_webhooks_title", body: "change_webhooks_body" },
  { tag: "tag_migration", tone: "amber", title: "change_reindex_title", body: "change_reindex_body" },
  { tag: "tag_new", tone: "green", title: "change_hold_title", body: "change_hold_body" },
  { tag: "tag_new", tone: "green", title: "change_hours_title", body: "change_hours_body" },
  { tag: "tag_fix", tone: "grey", title: "change_housenumbers_title", body: "change_housenumbers_body" },
  { tag: "tag_fix", tone: "grey", title: "change_sip_title", body: "change_sip_body" },
];

const SECURITY_CHANGES: { tag: ChangeKey; tone: Tone; title: ChangeKey; body: ChangeKey }[] = [
  { tag: "tag_security", tone: "red", title: "change_unauth_title", body: "change_unauth_body" },
  { tag: "tag_fix", tone: "grey", title: "change_cookies_title", body: "change_cookies_body" },
];

const HISTORY: { version: string; when: string; note: ChangeKey; tag?: ChangeKey }[] = [
  { version: "v1.4.2", when: "11 Aug", note: "history_142", tag: "history_running" },
  { version: "v1.4.1", when: "28 Jul", note: "history_141" },
  { version: "v1.4.0", when: "9 Jul", note: "history_140" },
  { version: "v1.3.4", when: "2 Jun", note: "history_134", tag: "history_rolled_back" },
  { version: "v1.3.3", when: "19 May", note: "history_133" },
];

type MarkState = "done" | "run" | "warn" | "wait";

function markStyle(state: MarkState) {
  return {
    borderColor:
      state === "done"
        ? "var(--od-green-border)"
        : state === "run"
          ? "var(--od-violet-border)"
          : state === "warn"
            ? "var(--od-amber-border)"
            : "var(--od-border-7)",
    background:
      state === "done"
        ? "rgba(63,185,132,.11)"
        : state === "run"
          ? "rgba(139,124,255,.13)"
          : state === "warn"
            ? "var(--od-amber-bg)"
            : "transparent",
    color:
      state === "done"
        ? "var(--od-green-text)"
        : state === "run"
          ? "var(--od-violet-3)"
          : "var(--od-amber-text)",
    animation: state === "run" ? "od-spin 1.1s linear infinite" : "none",
  };
}

function markGlyph(state: MarkState) {
  return state === "done" ? "✓" : state === "run" ? "◐" : state === "warn" ? "!" : "";
}

function Mark({ state }: { state: MarkState }) {
  return (
    <span
      className="inline-flex size-5 flex-none items-center justify-center rounded-full border text-[11px] leading-none font-bold"
      style={markStyle(state)}
    >
      {markGlyph(state)}
    </span>
  );
}

export function Update({ locale, t }: { locale: Locale; t: UpdateDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [notify, setNotify] = useState(true);

  const running = state === "running";
  const current = state === "current";
  const security = state === "security";
  const hasRelease = !current && !running;

  const severity = security ? "red" : current ? "ok" : "amber";
  const verdict = current
    ? {
        title: t.verdict_current_title,
        body: t.verdict_current_body,
        meta: t.verdict_current_meta,
      }
    : security
      ? {
          title: interpolate(t.verdict_security_title, { version: NEXT_SECURITY }),
          body: t.verdict_security_body,
          meta: t.verdict_security_meta,
        }
      : {
          title: interpolate(t.verdict_default_title, { version: NEXT }),
          body: t.verdict_default_body,
          meta: t.verdict_default_meta,
        };

  const preflight: { label: string; note: string; state: MarkState }[] = [
    { label: t.preflight_snapshot, note: t.preflight_snapshot_note, state: "done" },
    { label: t.preflight_disk, note: t.preflight_disk_note, state: "done" },
    { label: t.preflight_idle, note: t.preflight_idle_note, state: "done" },
    // A security release breaks nothing, so the webhook warning does not apply to it.
    ...(security
      ? []
      : [
          {
            label: t.preflight_webhooks,
            note: t.preflight_webhooks_note,
            state: "warn" as MarkState,
          },
        ]),
  ];

  const tasks: { name: string; meta: string; state: MarkState }[] = [
    { name: t.task_snapshot, meta: t.task_meta_snapshot, state: "done" },
    { name: t.task_containers, meta: t.task_meta_containers, state: "done" },
    {
      name: security ? t.task_patch : t.task_reindex,
      meta: t.task_meta_running,
      state: "run",
    },
    { name: t.task_migration, meta: t.task_meta_migration, state: "wait" },
    { name: t.task_restart, meta: t.task_meta_restart, state: "wait" },
    { name: t.task_verify, meta: "", state: "wait" },
  ];

  const changes = security ? SECURITY_CHANGES : CHANGES;

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="settings" liveCalls={0} />
      </div>

      <StatePreview
        state={state}
        onChange={setState}
        states={["default", "security", "running", "current"]}
        labels={{
          default: "Update ready",
          security: "Security",
          running: "Installing",
          current: "Up to date",
        }}
      />

      <div className="mx-auto max-w-[1000px] p-[26px_28px_80px]">
        <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-[14px]">
          <div className="min-w-0 max-w-[64ch] flex-[1_1_320px]">
            <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
              {t.title}
            </h1>
            {/* Nothing installs itself on a machine that answers a business phone. */}
            <p className="text-od-muted-4 mt-[6px] text-pretty">
              {interpolate(t.running_since, { version: CURRENT })}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="border-od-border-2 text-od-muted hover:text-od-text-2 hover:bg-od-raise-4 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] text-[13px] whitespace-nowrap"
            >
              {t.check_again}
            </button>
            <button
              type="button"
              disabled={current}
              onClick={() => setState("running")}
              className="rounded-[7px] border p-[9px_15px] text-[13px] font-semibold whitespace-nowrap"
              style={{
                borderColor: current ? "var(--od-border-7)" : "var(--od-violet-border)",
                background: current ? "transparent" : "var(--od-violet)",
                color: current ? "var(--od-faint-2)" : "#fff",
                cursor: current ? "default" : "pointer",
                pointerEvents: current ? "none" : "auto",
              }}
            >
              {current
                ? t.start_current
                : security
                  ? t.start_security
                  : interpolate(t.start_update, { version: NEXT })}
            </button>
          </div>
        </div>

        <div
          className="mt-5 flex flex-wrap items-start gap-x-4 gap-y-3 rounded-[11px] border p-[15px_17px]"
          style={{
            borderColor:
              severity === "red"
                ? "var(--od-red-border-3)"
                : severity === "amber"
                  ? "var(--od-amber-border-2)"
                  : "var(--od-line)",
            background:
              severity === "red"
                ? "var(--od-red-bg-4)"
                : severity === "amber"
                  ? "var(--od-amber-bg-2)"
                  : "var(--od-panel-deep-2)",
          }}
        >
          <span
            className="mt-[5px] size-[11px] flex-none rounded-full"
            style={{
              background:
                severity === "red"
                  ? "#F0605E"
                  : severity === "amber"
                    ? "var(--od-amber)"
                    : "var(--od-green)",
              animation: severity === "red" ? "od-ring 1.6s ease-out infinite" : "none",
            }}
          />
          <div className="min-w-[240px] flex-[1_1_320px]">
            <div
              className="text-[16px] font-semibold"
              style={{
                color:
                  severity === "red"
                    ? "var(--od-red-text-3)"
                    : severity === "amber"
                      ? "var(--od-amber-text-2)"
                      : "var(--od-text)",
              }}
            >
              {verdict.title}
            </div>
            <div
              className="mt-1 max-w-[70ch] text-[13px] text-pretty"
              style={{
                color:
                  severity === "red"
                    ? "var(--od-red-text-6)"
                    : severity === "amber"
                      ? "var(--od-amber-text-3)"
                      : "var(--od-muted)",
              }}
            >
              {verdict.body}
            </div>
          </div>
          <span className="text-od-faint flex-none text-[12px] whitespace-nowrap">
            {verdict.meta}
          </span>
        </div>

        {running ? (
          <div className="mt-4 overflow-hidden rounded-[11px] border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.06)]">
            <div className="border-b border-[color:var(--od-violet-border)] p-[15px_17px]">
              <div className="flex flex-wrap items-center gap-x-[14px] gap-y-[10px]">
                <span
                  className="inline-flex size-5 flex-none items-center justify-center rounded-full border border-[color:var(--od-violet-border)] text-[11px] text-[color:var(--od-violet-3)]"
                  style={{ animation: "od-spin 1.1s linear infinite" }}
                >
                  ◐
                </span>
                <span className="text-od-text min-w-0 flex-[1_1_240px] font-semibold">
                  {interpolate(t.running_title, { version: security ? NEXT_SECURITY : NEXT })}
                </span>
                <span className="text-od-muted-4 flex-none text-[12px]">{t.running_step}</span>
              </div>
              {/* When the downtime lands, and what a caller hears during it. */}
              <div className="text-od-muted-2 mt-[11px] text-[12.5px] text-pretty">
                {t.running_note}
              </div>
            </div>
            {tasks.map((task, index) => (
              <div
                key={task.name}
                className="flex flex-wrap items-center gap-x-3 gap-y-2 p-[11px_17px]"
                style={{ borderTop: index === 0 ? "none" : "1px solid var(--od-raise-6)" }}
              >
                <Mark state={task.state} />
                <span
                  className="min-w-[200px] flex-[1_1_240px] text-[13.5px]"
                  style={{ color: index < 3 ? "var(--od-text-3)" : "var(--od-faint-2)" }}
                >
                  {task.name}
                </span>
                <span className="text-od-faint-2 flex-none text-[11.5px] whitespace-nowrap">
                  {task.meta}
                </span>
              </div>
            ))}
          </div>
        ) : null}

        {hasRelease ? (
          <section className="mt-[22px] flex flex-wrap items-start gap-4">
            <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,400px)] flex-[2_1_420px] rounded-[10px] border">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px] p-[16px_18px_10px]">
                <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
                  {t.changes_heading}
                </h2>
                <span dir="ltr" className="mono ltr-data text-od-faint text-[12px]">
                  {`${CURRENT} → ${security ? NEXT_SECURITY : NEXT}`}
                </span>
              </div>
              {changes.map((change) => {
                const tone = TONES[change.tone];
                return (
                  <div
                    key={change.title}
                    className="flex flex-wrap items-start gap-x-3 gap-y-2 border-t border-[color:var(--od-raise-6)] p-[13px_18px]"
                  >
                    <span
                      className="mt-[2px] flex-none rounded-[5px] border p-[2px_9px] text-[10.5px] font-bold tracking-[.05em] uppercase whitespace-nowrap"
                      style={{
                        borderColor: tone.border,
                        background: tone.background,
                        color: tone.color,
                      }}
                    >
                      {t[change.tag]}
                    </span>
                    <div className="min-w-[200px] flex-[1_1_240px]">
                      <div className="text-od-text-3 font-medium text-pretty">
                        {t[change.title]}
                      </div>
                      <div className="text-od-muted-5 mt-[3px] max-w-[58ch] text-[12.5px] text-pretty">
                        {t[change.body]}
                      </div>
                    </div>
                  </div>
                );
              })}
              <div className="border-t border-[color:var(--od-raise-6)] p-[13px_18px]">
                <a
                  href={EXTERNAL.releases}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[13px]"
                >
                  {t.release_notes}
                </a>
              </div>
            </div>

            <div className="flex min-w-[min(100%,270px)] flex-[1_1_280px] flex-col gap-[14px]">
              <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
                <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
                  {t.preflight_heading}
                </h2>
                <div className="mt-[13px] flex flex-col gap-[11px]">
                  {preflight.map((check) => (
                    <div key={check.label} className="flex items-start gap-[10px]">
                      <Mark state={check.state} />
                      <div className="min-w-0 flex-[1_1_auto]">
                        <div className="text-od-text-3 text-[13.5px]">{check.label}</div>
                        <div className="text-od-faint mt-[2px] text-[12px] text-pretty">
                          {check.note}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border-od-line bg-od-panel-deep-2 rounded-[10px] border p-4">
                <div className="text-od-text-5 text-[13.5px] font-semibold">
                  {t.rollback_heading}
                </div>
                <div className="text-od-muted mt-[5px] text-[12.5px] text-pretty">
                  {t.rollback_body}
                </div>
                <Link href={`/${locale}/backup`} className="mt-[10px] inline-block text-[12.5px]">
                  {t.rollback_link}
                </Link>
              </div>

              <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-4">
                <div className="flex flex-wrap items-center justify-between gap-x-[14px] gap-y-[10px]">
                  <span className="text-od-text-3 text-[13.5px]">{t.notify_label}</span>
                  <button
                    type="button"
                    onClick={() => setNotify((value) => !value)}
                    aria-label={t.notify_label}
                    aria-pressed={notify}
                    className="inline-flex h-[21px] w-[38px] flex-none cursor-pointer items-center rounded-full border p-[2px]"
                    style={{
                      borderColor: notify ? "var(--od-violet)" : "var(--od-border-7)",
                      background: notify ? "var(--od-violet)" : "var(--od-raise)",
                      justifyContent: notify ? "flex-end" : "flex-start",
                    }}
                  >
                    <span
                      className="block size-[15px] rounded-full"
                      style={{ background: notify ? "#fff" : "var(--od-stroke-5)" }}
                    />
                  </button>
                </div>
                <div className="text-od-faint mt-[5px] text-[12px] text-pretty">
                  {t.notify_note}
                </div>
              </div>
            </div>
          </section>
        ) : null}

        {current ? (
          <div className="border-od-border-6 bg-od-panel-deep-2 mt-5 rounded-[10px] border border-dashed p-[34px_28px]">
            <h3 className="m-0 text-[18px] font-semibold">{t.current_title}</h3>
            <p className="text-od-muted mt-[9px] max-w-[58ch] text-pretty">
              {interpolate(t.current_body, { version: CURRENT })}
            </p>
          </div>
        ) : null}

        <section className="mt-6">
          <h2 className="text-od-muted-4 mt-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.history_heading}
          </h2>
          <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
            {HISTORY.map((entry, index) => (
              <div
                key={entry.version}
                className="flex flex-wrap items-center gap-x-[14px] gap-y-2 p-[12px_16px]"
                style={{ borderTop: index === 0 ? "none" : "1px solid var(--od-raise-6)" }}
              >
                <span dir="ltr" className="mono ltr-data text-od-text-3 w-[62px] flex-none text-[13px]">
                  {entry.version}
                </span>
                <span
                  dir="ltr"
                  className="mono ltr-data text-od-faint-2 w-[92px] flex-none text-[11.5px]"
                >
                  {entry.when}
                </span>
                <span className="text-od-muted-5 min-w-[200px] flex-[1_1_240px] text-[12.5px] text-pretty">
                  {t[entry.note]}
                </span>
                {entry.tag ? (
                  <span
                    className="flex-none rounded-[5px] border p-[2px_9px] text-[10.5px] font-semibold whitespace-nowrap"
                    style={{
                      borderColor:
                        entry.tag === "history_running"
                          ? "var(--od-green-border)"
                          : "var(--od-amber-border)",
                      background:
                        entry.tag === "history_running"
                          ? "rgba(63,185,132,.10)"
                          : "var(--od-amber-bg)",
                      color:
                        entry.tag === "history_running"
                          ? "var(--od-green-text)"
                          : "var(--od-amber-text)",
                    }}
                  >
                    {t[entry.tag]}
                  </span>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
