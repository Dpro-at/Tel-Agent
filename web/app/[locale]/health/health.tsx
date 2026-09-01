"use client";

import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  systemLog,
  systemStatus,
  type LogFilter,
  type ServiceRow,
  type ServiceState,
  type SystemStatus,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { HealthDictionary } from "./page";

type Key = keyof HealthDictionary;

/**
 * The name and the consequence for each row the server can report.
 *
 * The server decides *state*; this table decides *wording*. Keeping them apart is what
 * lets the copy be translated into three languages while the state stays one word the
 * API is responsible for — and it means a service the server has not heard of shows up
 * as itself rather than as a blank row.
 */
const SERVICE_COPY: Record<string, { name: Key; impact: Key }> = {
  api: { name: "service_api", impact: "service_api_impact" },
  db: { name: "service_db", impact: "service_db_impact" },
  smtp: { name: "service_smtp", impact: "service_smtp_impact" },
  web_channel: { name: "service_web_chat", impact: "service_web_chat_impact" },
  sip: { name: "service_sip", impact: "service_sip_impact" },
  llm: { name: "service_llm", impact: "service_llm_impact" },
  stt: { name: "service_stt", impact: "service_stt_impact" },
  tts: { name: "service_tts", impact: "service_tts_impact" },
  // Milestone 9: one row per channel kind that exists, straight from the
  // transports' own reports. The `latency_ms` on these rows is the last reply's
  // whole journey, generation to delivery, not a probe.
  channel_telegram: { name: "service_ch_telegram", impact: "service_ch_telegram_impact" },
  channel_email: { name: "service_ch_email", impact: "service_ch_email_impact" },
  channel_whatsapp: { name: "service_ch_whatsapp", impact: "service_ch_whatsapp_impact" },
  channel_messenger: { name: "service_ch_messenger", impact: "service_ch_messenger_impact" },
  channel_instagram: { name: "service_ch_instagram", impact: "service_ch_instagram_impact" },
  channel_discord: { name: "service_ch_discord", impact: "service_ch_discord_impact" },
  channel_slack: { name: "service_ch_slack", impact: "service_ch_slack_impact" },
};

const LOG_FILTERS: { id: LogFilter; label: Key }[] = [
  { id: "all", label: "log_all" },
  { id: "errors", label: "log_errors" },
  { id: "warnings", label: "log_warnings" },
  { id: "calls", label: "log_calls" },
];

/** Bytes as a person reads them. Binary units, because a disk reports binary. */
function bytes(value: number | null): string {
  if (value === null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size < 10 && unit > 0 ? size.toFixed(1) : Math.round(size)} ${units[unit]}`;
}

/** The colour vocabulary, in one place so no row invents its own. */
function tone(state: ServiceState) {
  if (state === "down")
    return {
      text: "var(--od-red-text-4)",
      border: "var(--od-red-border)",
      background: "rgba(240,96,94,.11)",
      dot: "var(--od-red-text-4)",
    };
  if (state === "degraded")
    return {
      text: "var(--od-amber-text)",
      border: "var(--od-amber-border)",
      background: "var(--od-amber-bg)",
      dot: "var(--od-amber-text)",
    };
  // Not configured is deliberately grey, not green and not red. It is neither working
  // nor broken, and colouring it either way is the lie this screen must not tell.
  if (state === "not_configured")
    return {
      text: "var(--od-faint-2)",
      border: "var(--od-border-7)",
      background: "transparent",
      dot: "var(--od-border-3)",
    };
  return {
    text: "var(--od-green-text)",
    border: "var(--od-green-border)",
    background: "rgba(63,185,132,.10)",
    dot: "var(--od-green)",
  };
}

function stateLabel(t: HealthDictionary, state: ServiceState): string {
  if (state === "down") return t.state_down;
  if (state === "degraded") return t.state_slow;
  if (state === "not_configured") return t.state_not_configured;
  return t.state_running;
}

export function Health({ locale, t }: { locale: Locale; t: HealthDictionary }) {
  const [level, setLevel] = useState<LogFilter>("all");
  const [openLine, setOpenLine] = useState<number | null>(null);

  const status = useResource<SystemStatus>(() => systemStatus());
  // The log is its own request because the filter chips change it and nothing else.
  // Refetching the whole health report to change a chip would make the services flicker.
  const log = useResource(() => systemLog(level, 100), [level]);

  if (status.loading && status.data === null) {
    return <Frame locale={locale}>{<Notice title={t.loading} />}</Frame>;
  }

  if (status.error !== null && status.data === null) {
    return (
      <Frame locale={locale}>
        <Notice
          title={status.error.kind === "offline" ? t.error_offline_title : t.error_failed_title}
          body={status.error.kind === "offline" ? t.error_offline_body : status.error.message}
          action={{ label: t.retry, onClick: status.reload }}
          bad
        />
      </Frame>
    );
  }

  const report = status.data as SystemStatus;
  const unbuilt = report.services.filter((s) => s.state === "not_configured").length;
  const verdict =
    report.verdict === "down"
      ? { title: t.verdict_down_title, body: t.verdict_down_body_real }
      : report.verdict === "degraded"
        ? { title: t.verdict_degraded_title, body: t.verdict_degraded_body_real }
        : {
            title: t.verdict_ok_title,
            body: interpolate(t.verdict_ok_body_real, { unbuilt: String(unbuilt) }),
          };

  const down = report.verdict === "down";
  const degraded = report.verdict === "degraded";

  const storageRows: { label: Key; note: Key; value: number }[] = [
    { label: "storage_recordings", note: "storage_recordings_note", value: report.storage.parts.recordings ?? 0 },
    { label: "storage_backups", note: "storage_backups_note", value: report.storage.parts.backups ?? 0 },
  ];

  return (
    <Frame locale={locale}>
      <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-[14px]">
        <div className="min-w-0 max-w-[64ch] flex-[1_1_320px]">
          <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
            {t.title}
          </h1>
          <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => {
              status.reload();
              log.reload();
            }}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13px] font-medium whitespace-nowrap"
          >
            {t.recheck}
          </button>
        </div>
      </div>

      <div
        className="mt-5 flex flex-wrap items-start gap-x-4 gap-y-3 rounded-[11px] border p-[15px_17px]"
        style={{
          borderColor: down
            ? "var(--od-red-border-3)"
            : degraded
              ? "var(--od-amber-border-2)"
              : "var(--od-line)",
          background: down
            ? "var(--od-red-bg-4)"
            : degraded
              ? "var(--od-amber-bg-2)"
              : "var(--od-panel-deep-2)",
        }}
      >
        <span
          className="mt-[5px] size-[11px] flex-none rounded-full"
          style={{
            background: down ? "#F0605E" : degraded ? "var(--od-amber)" : "var(--od-green)",
            animation: down ? "od-ring 1.6s ease-out infinite" : "none",
          }}
        />
        <div className="min-w-[240px] flex-[1_1_320px]">
          <div
            className="text-[16px] font-semibold"
            style={{
              color: down
                ? "var(--od-red-text-3)"
                : degraded
                  ? "var(--od-amber-text-2)"
                  : "var(--od-text)",
            }}
          >
            {verdict.title}
          </div>
          <div
            className="mt-1 max-w-[70ch] text-[13px] text-pretty"
            style={{
              color: down
                ? "var(--od-red-text-6)"
                : degraded
                  ? "var(--od-amber-text-3)"
                  : "var(--od-muted)",
            }}
          >
            {verdict.body}
          </div>
        </div>
        <span className="text-od-faint flex-none text-[12px] whitespace-nowrap">
          {status.loading ? t.loading : t.checked_now}
        </span>
      </div>

      <section className="mt-[22px]">
        <h2 className="text-od-muted-4 mt-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.services_heading}
        </h2>
        <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
          {report.services.map((service: ServiceRow, index: number) => {
            const colour = tone(service.state);
            const copy = SERVICE_COPY[service.id];
            return (
              <div
                key={service.id}
                className={`flex flex-wrap items-start gap-x-[14px] gap-y-[10px] p-[14px_16px] ${
                  index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
                }`}
              >
                <span
                  className="mt-[6px] size-[9px] flex-none rounded-full"
                  style={{
                    background: colour.dot,
                    animation: service.state === "down" ? "od-ring 1.6s ease-out infinite" : "none",
                  }}
                />
                <div className="min-w-[200px] flex-[1_1_240px]">
                  <div className="flex flex-wrap items-center gap-[9px]">
                    <span className="text-od-text-3 font-medium">
                      {copy ? t[copy.name] : service.id}
                    </span>
                    <span
                      className="rounded-[5px] border p-[2px_9px] text-[11px] font-semibold whitespace-nowrap"
                      style={{
                        borderColor: colour.border,
                        background: colour.background,
                        color: colour.text,
                      }}
                    >
                      {stateLabel(t, service.state)}
                    </span>
                  </div>
                  <div className="text-od-muted-5 mt-[3px] max-w-[62ch] text-[12.5px] text-pretty">
                    {service.state === "not_configured"
                      ? t.not_configured_note
                      : copy
                        ? t[copy.impact]
                        : ""}
                  </div>
                  {/* The server's own words about the failure — the host and port a
                      refused connection names is the part an operator acts on. */}
                  {service.detail ? (
                    <div
                      dir="ltr"
                      className="mono ltr-data mt-[6px] text-[11.5px] [overflow-wrap:anywhere]"
                      style={{ color: colour.text }}
                    >
                      {service.detail}
                    </div>
                  ) : null}
                </div>
                <div className="min-w-[96px] flex-none text-end">
                  {service.latency_ms === null ? null : (
                    <div dir="ltr" className="mono ltr-data text-od-muted-4 text-[12px]">
                      {service.latency_ms} ms
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="mt-6 flex flex-wrap items-start gap-4">
        <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,280px)] flex-[1_1_300px] rounded-[10px] border p-[18px]">
          <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.storage_heading}
          </h2>
          <div className="mt-[14px] flex flex-col gap-[14px]">
            {storageRows.map((entry) => {
              const total = report.storage.total_bytes;
              const fraction = total && total > 0 ? entry.value / total : 0;
              return (
                <div key={entry.label}>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-[14px] gap-y-2">
                    <span className="text-od-text-3 text-[13.5px]">{t[entry.label]}</span>
                    <span dir="ltr" className="mono ltr-data text-od-muted-4 text-[12px]">
                      {bytes(entry.value)}
                    </span>
                  </div>
                  <div className="mt-[7px] h-1 rounded-full bg-[var(--od-raise-4)]">
                    <span
                      className="block h-1 rounded-full"
                      style={{
                        // A part that rounds to nothing still gets a sliver, so the row
                        // does not read as "this does not exist".
                        width: `${Math.max(fraction * 100, entry.value > 0 ? 2 : 0)}%`,
                        background: fraction > 0.85 ? "var(--od-amber)" : "var(--od-violet)",
                      }}
                    />
                  </div>
                  <div className="text-od-faint mt-[5px] text-[12px] text-pretty">
                    {t[entry.note]}
                  </div>
                </div>
              );
            })}
            <div className="border-od-line border-t pt-[12px]">
              <span className="text-od-muted-4 text-[12px]">
                {interpolate(t.storage_free_of, {
                  free: bytes(report.storage.free_bytes),
                  total: bytes(report.storage.total_bytes),
                })}
              </span>
            </div>
          </div>
        </div>

        <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,280px)] flex-[1_1_300px] rounded-[10px] border p-[18px]">
          <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.latency_heading}
          </h2>
          {/* No calls have happened, so there is no series. An empty chart drawn from
              zeroes would read as "every call was instant", which is worse than a
              sentence saying there is nothing yet. */}
          <p className="text-od-muted-5 mt-4 text-[12.5px] text-pretty">{t.latency_empty}</p>
        </div>
      </section>

      <section className="mt-6">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
          <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.log_heading}
          </h2>
          <div className="flex flex-wrap gap-2">
            {LOG_FILTERS.map((entry) => {
              const on = level === entry.id;
              return (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => setLevel(entry.id)}
                  className={`cursor-pointer rounded-full border p-[6px_12px] text-[12.5px] whitespace-nowrap ${
                    on
                      ? "border-od-stroke bg-od-raise-10 text-od-text"
                      : "border-od-border-7 text-od-muted-4 bg-transparent"
                  }`}
                >
                  {t[entry.label]}
                </button>
              );
            })}
          </div>
        </div>

        {/* Machine output: never translated, always left to right. */}
        <div
          dir="ltr"
          className="border-od-line bg-od-canvas-2 overflow-hidden rounded-[10px] border"
        >
          {log.data === null || log.data.entries.length === 0 ? (
            <p className="text-od-faint m-0 p-[14px] text-[12.5px]">
              {log.loading ? t.loading : t.log_empty}
            </p>
          ) : (
            log.data.entries.map((line, index) => (
              <div
                key={`${line.time}-${index}`}
                className={index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"}
              >
                <div className="flex flex-wrap items-start gap-x-[14px] gap-y-[6px] p-[9px_14px]">
                  <span className="mono ltr-data text-od-faint-2 flex-none text-[11.5px]">
                    {line.time.slice(11, 19)}
                  </span>
                  <span
                    className="mono ltr-data w-[52px] flex-none text-[11px] font-semibold uppercase"
                    style={{
                      color:
                        line.level === "error" || line.level === "critical"
                          ? "var(--od-red-text-4)"
                          : line.level === "warning"
                            ? "var(--od-amber-text)"
                            : "var(--od-faint-2)",
                    }}
                  >
                    {line.level}
                  </span>
                  <span className="mono ltr-data text-od-faint w-[92px] flex-none text-[11.5px]">
                    {line.service}
                  </span>
                  <span className="mono ltr-data text-od-text-2 min-w-[200px] flex-[1_1_240px] text-[12px] [overflow-wrap:anywhere]">
                    {line.message}
                  </span>
                  {line.exception ? (
                    <button
                      type="button"
                      onClick={() => setOpenLine(openLine === index ? null : index)}
                      className="text-od-muted-4 hover:text-od-text-2 flex-none cursor-pointer border-0 bg-transparent text-[11.5px] underline"
                    >
                      {openLine === index ? t.log_hide_detail : t.log_show_detail}
                    </button>
                  ) : null}
                </div>
                {/* The traceback, which is the thing that was missing when this panel
                    was first built and could say only "unhandled exception". */}
                {openLine === index && line.exception ? (
                  <pre className="mono text-od-muted-5 m-0 overflow-x-auto p-[0_14px_12px] text-[11.5px] whitespace-pre">
                    {line.exception}
                  </pre>
                ) : null}
              </div>
            ))
          )}
        </div>

        {log.data ? (
          <p className="text-od-faint-2 mt-[10px] text-[12px] text-pretty">
            {interpolate(t.log_ring_note, { capacity: String(log.data.capacity) })}
          </p>
        ) : null}

        {/* A diagnostics bundle is shared with strangers, so what it excludes is stated. */}
        <div className="border-od-line bg-od-panel-deep-2 text-od-muted mt-[14px] rounded-[9px] border p-[13px_15px] text-[12.5px] text-pretty">
          {t.bundle_note}
        </div>
      </section>
    </Frame>
  );
}

function Frame({ locale, children }: { locale: Locale; children: React.ReactNode }) {
  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="settings" liveCalls={0} />
      </div>
      <div className="mx-auto max-w-[1080px] p-[26px_28px_80px]">{children}</div>
    </div>
  );
}

/** Loading and failure look the same everywhere, so they are written once. */
function Notice({
  title,
  body,
  action,
  bad = false,
}: {
  title: string;
  body?: string;
  action?: { label: string; onClick: () => void };
  bad?: boolean;
}) {
  return (
    <div
      className="mt-6 rounded-[11px] border p-[18px_20px]"
      style={{
        borderColor: bad ? "var(--od-red-border-3)" : "var(--od-line)",
        background: bad ? "var(--od-red-bg-4)" : "var(--od-panel-deep-2)",
      }}
    >
      <div
        className="text-[15px] font-semibold"
        style={{ color: bad ? "var(--od-red-text-3)" : "var(--od-text)" }}
      >
        {title}
      </div>
      {body ? (
        <p
          className="mt-[6px] max-w-[70ch] text-[13px] text-pretty"
          style={{ color: bad ? "var(--od-red-text-6)" : "var(--od-muted)" }}
        >
          {body}
        </p>
      ) : null}
      {action ? (
        <button
          type="button"
          onClick={action.onClick}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
        >
          {action.label}
        </button>
      ) : null}
    </div>
  );
}
