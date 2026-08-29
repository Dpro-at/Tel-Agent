"use client";

import Link from "next/link";

import { Sidebar } from "@/components/shell/sidebar";
import {
  conversationDetail,
  type ThreadDetail,
  type ThreadHandling,
  type ThreadMessage,
} from "@/lib/api";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { CallDetailDictionary } from "./page";

/** Three speaker types, distinct at a glance without being loud (§A6.4). */
const SPEAKERS: Record<
  ThreadMessage["speaker"],
  { color: string; background: string; border: string }
> = {
  caller: { color: "var(--od-muted-2)", background: "transparent", border: "transparent" },
  agent: {
    color: "var(--od-violet-3)",
    background: "rgba(139,124,255,.09)",
    border: "rgba(139,124,255,.26)",
  },
  human: {
    color: "var(--od-green-text)",
    background: "rgba(63,185,132,.09)",
    border: "rgba(63,185,132,.30)",
  },
};

const HANDLED: Record<
  ThreadHandling,
  { label: keyof CallDetailDictionary; color: string; background: string; border: string }
> = {
  ai: {
    label: "handled_agent",
    color: "var(--od-violet-3)",
    background: "rgba(139,124,255,.13)",
    border: "var(--od-violet-border)",
  },
  human: {
    label: "handled_human",
    color: "var(--od-green-text)",
    background: "rgba(63,185,132,.11)",
    border: "var(--od-green-border)",
  },
  blocked: {
    label: "handled_blocked",
    color: "var(--od-red-text-4)",
    background: "rgba(240,96,94,.11)",
    border: "var(--od-red-border)",
  },
};

/** A position in the call, from `ts_ms` - mm:ss into the recording, not a clock. */
function offset(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function minutes(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function CallDetail({
  locale,
  t,
  id,
}: {
  locale: Locale;
  t: CallDetailDictionary;
  id: number;
}) {
  const thread = useResource<ThreadDetail>(() => conversationDetail(id), [id]);

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="calls" />
      </div>

      <div className="mx-auto max-w-[1560px] p-[26px_28px_72px]">
        <div className="text-od-faint mb-4 flex flex-wrap items-center gap-x-[18px] gap-y-[10px] text-[13px]">
          <Link href={`/${locale}/calls`} className="text-od-muted hover:underline">
            {t.breadcrumb_calls}
          </Link>
          <span className="text-[color:var(--od-border-11)]">/</span>
          <span dir="ltr" className="mono ltr-data text-[12px]">
            {id}
          </span>
        </div>

        {thread.loading && thread.data === null ? <DetailSkeleton /> : null}

        {thread.error !== null && thread.data === null ? (
          <div className="border-od-red-border-3 bg-od-red-bg-4 rounded-[10px] border p-[18px_20px]">
            <h3 className="m-0 text-[16px] font-semibold text-[color:var(--od-red-text-3)]">
              {thread.error.kind === "offline" ? t.error_offline_title : t.error_failed_title}
            </h3>
            <p className="mt-[6px] max-w-[62ch] text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
              {thread.error.message}
            </p>
            <button
              type="button"
              onClick={thread.reload}
              className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
            >
              {t.retry}
            </button>
          </div>
        ) : null}

        {thread.data !== null ? <Loaded locale={locale} t={t} thread={thread.data} /> : null}
      </div>
    </div>
  );
}

function Loaded({
  locale,
  t,
  thread,
}: {
  locale: Locale;
  t: CallDetailDictionary;
  thread: ThreadDetail;
}) {
  const spoken = thread.messages.filter((line) => !line.is_whisper);
  const whispers = thread.messages.filter((line) => line.is_whisper);
  const handled = thread.handling ? HANDLED[thread.handling] : null;

  // The call's own metering when it exists; the wall clock otherwise.
  const seconds =
    thread.call?.billable_seconds ??
    (thread.ended_at !== null
      ? Math.max(
          0,
          Math.round(
            (new Date(thread.ended_at).getTime() - new Date(thread.started_at).getTime()) / 1000,
          ),
        )
      : null);

  return (
    <div>
      <header className="border-od-border flex flex-wrap items-start justify-between gap-x-10 gap-y-6 border-b pb-[22px]">
        <div className="min-w-[240px] flex-[1_1_300px]">
          {/* The phonebook's name when it has one; the number stays underneath -
              an annotation on the record, not a replacement for it. */}
          {thread.who_name ? (
            <>
              <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.015em]">
                {thread.who_name}
              </h1>
              <div dir="ltr" className="mono ltr-data text-od-muted mt-[6px] text-start text-[13.5px]">
                {thread.call?.from_e164 ?? thread.who}
              </div>
            </>
          ) : thread.call?.from_e164 ?? thread.who ? (
            <h1
              dir="ltr"
              className="text-od-text mono ltr-data m-0 text-start text-[26px] font-semibold tracking-[-0.015em]"
            >
              {thread.call?.from_e164 ?? thread.who}
            </h1>
          ) : (
            <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.015em]">
              {t.caller_unknown}
            </h1>
          )}
          {thread.call ? (
            <div className="text-od-muted-5 mt-[6px] text-[13px]">
              {thread.call.has_recording ? t.recording_kept : t.recording_none}
            </div>
          ) : null}
        </div>

        <div className="flex flex-wrap items-start gap-x-[34px] gap-y-[22px]">
          <Fact label={t.fact_date}>
            {new Date(thread.started_at).toLocaleDateString(locale, {
              day: "numeric",
              month: "long",
              year: "numeric",
            })}
            ,{" "}
            <span className="mono">
              {new Date(thread.started_at).toLocaleTimeString(locale, {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
          </Fact>
          <Fact label={t.fact_duration}>
            {seconds !== null ? <span className="mono">{minutes(seconds)}</span> : "—"}
          </Fact>
          <div>
            <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
              {t.fact_handled}
            </div>
            <div
              className="mt-[5px] inline-flex items-center gap-[7px] rounded-md border p-[4px_11px] text-[13px] font-medium"
              style={
                handled
                  ? {
                      borderColor: handled.border,
                      background: handled.background,
                      color: handled.color,
                    }
                  : {
                      borderColor: "var(--od-border-9)",
                      background: "var(--od-raise-5)",
                      color: "var(--od-muted-2)",
                    }
              }
            >
              <span>
                {handled
                  ? t[handled.label]
                  : thread.status === "open"
                    ? t.status_open
                    : t.status_closed}
              </span>
            </div>
          </div>
          <Fact label={t.fact_intent}>{thread.intent ?? "—"}</Fact>
        </div>
      </header>

      <div className="mt-[26px] flex flex-wrap items-start gap-[26px]">
        <section className="min-w-[min(100%,420px)] flex-[4_1_480px]">
          <div className="mb-[14px] flex flex-wrap items-baseline justify-between gap-[10px]">
            <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
              {t.transcript_heading}
            </h2>
            <span className="text-od-faint-2 text-[12px]">{t.transcript_note}</span>
          </div>

          {spoken.length === 0 ? (
            <div className="border-od-border-6 bg-od-panel-deep-2 rounded-[10px] border border-dashed p-[34px_28px]">
              <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
              <p className="text-od-muted mt-[10px] max-w-[60ch] text-pretty">{t.empty_body}</p>
            </div>
          ) : (
            <div className="flex flex-col text-[16px]">
              {spoken.map((line) => {
                const style = SPEAKERS[line.speaker];
                const speakerLabel =
                  line.speaker === "caller"
                    ? t.speaker_caller
                    : line.speaker === "agent"
                      ? t.speaker_agent
                      : t.speaker_human;
                return (
                  <div
                    key={line.id}
                    className="grid gap-[14px] border-b border-[color:var(--od-raise-6)] py-[11px]"
                    style={{ gridTemplateColumns: "max-content minmax(0,1fr)" }}
                  >
                    <span dir="ltr" className="mono ltr-data text-od-faint pt-[3px] text-[12px]">
                      {offset(line.ts_ms)}
                    </span>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className="rounded border p-[1px_8px] text-[11.5px] font-semibold tracking-[.04em] uppercase"
                          style={{
                            color: style.color,
                            background: style.background,
                            borderColor: style.border,
                          }}
                        >
                          {speakerLabel}
                        </span>
                        {/* Confidence and language exist only on spoken-and-heard
                            lines; a typed line simply has neither. */}
                        {line.stt_confidence !== null ? (
                          <span dir="ltr" className="mono ltr-data text-od-faint-2 text-[11.5px]">
                            {Math.round(line.stt_confidence * 100)}%
                          </span>
                        ) : null}
                        {line.language !== null ? (
                          <span dir="ltr" className="mono ltr-data text-od-faint-2 text-[11.5px]">
                            {line.language}
                          </span>
                        ) : null}
                      </div>
                      {/* What was said, verbatim - never translated. */}
                      <div
                        dir="ltr"
                        className="mt-[5px] text-start leading-[1.6] text-pretty text-[color:var(--od-text-4)]"
                      >
                        {line.text}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* §A6.4: never inline with what the caller heard. */}
        <section className="max-w-[340px] min-w-[min(100%,260px)] flex-[1_1_260px]">
          <h2 className="text-od-muted-4 mb-[14px] text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.whisper_heading}
          </h2>
          <div
            className="border-od-border-10 rounded-[10px] border border-dashed p-[14px]"
            style={{
              background:
                "repeating-linear-gradient(135deg, var(--od-panel-deep) 0 10px, var(--od-panel-deep-4) 10px 20px)",
            }}
          >
            <div className="border-od-border-8 flex items-start gap-[9px] border-b border-dashed pb-3">
              <span className="border-od-stroke-2 flex size-[18px] flex-none items-center justify-center rounded border text-[11px] text-[color:var(--od-muted-3)]">
                ✕
              </span>
              <div className="text-[12.5px] text-pretty text-[color:var(--od-muted-3)]">
                {t.whisper_internal}
              </div>
            </div>

            {whispers.length === 0 ? (
              <div className="text-od-faint mt-3 text-[13px]">{t.whisper_none}</div>
            ) : (
              <div className="mt-3 flex flex-col gap-3">
                {whispers.map((whisper) => (
                  <div
                    key={whisper.id}
                    className="border-od-stroke-3 bg-od-panel-deep-8 rounded-lg border border-dashed p-3"
                  >
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span dir="ltr" className="mono ltr-data text-od-faint text-[12px]">
                        {offset(whisper.ts_ms)}
                      </span>
                      <span className="text-[11.5px] font-semibold tracking-[.04em] uppercase text-[color:var(--od-muted-2)]">
                        {t.whisper_from}
                      </span>
                    </div>
                    {/* What the operator typed, verbatim. */}
                    <div
                      dir="ltr"
                      className="text-[14px] leading-[1.6] text-start text-pretty italic text-[color:var(--od-text-6)]"
                    >
                      {whisper.text}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {thread.summary ? (
          <aside className="flex max-w-[380px] min-w-[min(100%,290px)] flex-[1_1_300px] flex-col gap-4">
            <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[16px_18px]">
              <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
                {t.summary_heading}
              </h2>
              <p className="mt-[10px] mb-0 text-[14.5px] leading-[1.66] text-pretty text-[color:var(--od-text-5)]">
                {thread.summary}
              </p>
            </div>
          </aside>
        ) : null}
      </div>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">{label}</div>
      <div className="text-od-text-3 mt-[5px]">{children}</div>
    </div>
  );
}

function DetailSkeleton() {
  const shimmer = {
    background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  };

  return (
    <div>
      <div className="border-od-raise-12 h-[110px] rounded-[10px] border" style={shimmer} />
      <div className="mt-[26px] flex flex-wrap gap-[26px]">
        <div
          className="border-od-raise-12 h-[420px] min-w-[min(100%,420px)] flex-[4_1_480px] rounded-[10px] border"
          style={shimmer}
        />
        <div
          className="border-od-raise-12 h-[260px] max-w-[340px] min-w-[min(100%,260px)] flex-[1_1_260px] rounded-[10px] border"
          style={shimmer}
        />
      </div>
    </div>
  );
}
