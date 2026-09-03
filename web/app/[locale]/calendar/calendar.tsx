"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { getAvailability, type Availability } from "@/lib/api";
import { useResource } from "@/lib/use-resource";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { CalendarDictionary } from "./page";

const START_HOUR = 8;
const END_HOUR = 19;
const SLOT_MINUTES = 15;
const ROW_HEIGHT = 26;
const ROWS = ((END_HOUR - START_HOUR) * 60) / SLOT_MINUTES;
const DAYS_SHOWN = 7;
const GRID_COLUMNS = `64px repeat(${DAYS_SHOWN}, minmax(0,1fr))`;

/** The Monday of the week holding `date`, as a local date. */
function mondayOf(date: Date): Date {
  const copy = new Date(date);
  copy.setHours(0, 0, 0, 0);
  copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7));
  return copy;
}

function isoDay(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/**
 * One busy interval cut down to the piece that falls on `day` inside the drawn hours,
 * as fractional grid rows. Instants arrive in UTC and are placed on the reader's own
 * clock — the same clock the grid's hour labels use.
 */
function rowsOnDay(
  busyStart: Date,
  busyEnd: Date,
  day: Date,
): { top: number; span: number } | null {
  const dayStart = new Date(day);
  dayStart.setHours(START_HOUR, 0, 0, 0);
  const dayEnd = new Date(day);
  dayEnd.setHours(END_HOUR, 0, 0, 0);
  const start = busyStart > dayStart ? busyStart : dayStart;
  const end = busyEnd < dayEnd ? busyEnd : dayEnd;
  if (end <= start) return null;
  const minutes = (value: Date) => (value.getTime() - dayStart.getTime()) / 60000;
  const top = minutes(start) / SLOT_MINUTES;
  const span = Math.max(1, (minutes(end) - minutes(start)) / SLOT_MINUTES);
  return { top, span };
}

export function Calendar({ locale, t }: { locale: Locale; t: CalendarDictionary }) {
  // Which week is on screen, as an offset from the current one. The server is asked
  // for exactly the days drawn, so paging is a refetch, not a client-side filter.
  const [weekOffset, setWeekOffset] = useState(0);

  const monday = useMemo(() => {
    const base = mondayOf(new Date());
    base.setDate(base.getDate() + weekOffset * 7);
    return base;
  }, [weekOffset]);

  const availability = useResource<Availability>(
    () => getAvailability(isoDay(monday), DAYS_SHOWN),
    [isoDay(monday)],
  );

  const days = useMemo(
    () =>
      Array.from({ length: DAYS_SHOWN }, (_, index) => {
        const day = new Date(monday);
        day.setDate(day.getDate() + index);
        return day;
      }),
    [monday],
  );

  const weekdayName = useMemo(
    () => new Intl.DateTimeFormat(locale, { weekday: "short" }),
    [locale],
  );
  const rangeLabel = useMemo(
    () => new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric" }),
    [locale],
  );
  const timeLabel = useMemo(
    () => new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hour12: false }),
    [locale],
  );

  const today = isoDay(new Date());
  const data = availability.data;
  const state = data?.state;
  const busy = useMemo(
    () =>
      (data?.busy ?? []).map((period) => ({
        start: new Date(period.start),
        end: new Date(period.end),
      })),
    [data],
  );

  const counts = days.map(
    (day) =>
      busy.filter((period) => rowsOnDay(period.start, period.end, day) !== null).length,
  );

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="calendar" />
      </div>

      {state === "unreachable" ? (
        <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-4">
          <span
            className="size-[10px] flex-none rounded-full bg-[#F0605E]"
            style={{ animation: "od-ring 1.6s ease-out infinite" }}
          />
          <div className="min-w-[240px] flex-[1_1_340px]">
            <div className="text-[16px] font-semibold text-[color:var(--od-red-text)]">
              {t.offline_title}
            </div>
            <div className="mt-[3px] text-[color:var(--od-red-text-2)]">{t.offline_body}</div>
          </div>
          <button
            type="button"
            onClick={availability.reload}
            className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[9px_15px] font-medium text-[color:var(--od-red-text-3)]"
          >
            {t.offline_retry}
          </button>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1560px] p-[22px_28px_70px]">
        {availability.error ? (
          <FetchFailed message={availability.error.message} retry={availability.reload} t={t} />
        ) : null}
        {!data && availability.loading ? <CalendarSkeleton /> : null}
        {state === "rejected" ? <CredentialsRejected locale={locale} source={data?.source} t={t} /> : null}
        {state === "unconfigured" ? <NotConnected locale={locale} t={t} /> : null}

        {state === "ok" || state === "unreachable" ? (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div>
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                <div dir="ltr" className="text-od-muted-4 ltr-data mt-[5px] text-start">
                  {rangeLabel.formatRange
                    ? rangeLabel.formatRange(days[0], days[DAYS_SHOWN - 1])
                    : `${rangeLabel.format(days[0])} – ${rangeLabel.format(days[DAYS_SHOWN - 1])}`}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex gap-[2px]">
                  <button
                    type="button"
                    onClick={() => setWeekOffset((value) => value - 1)}
                    aria-label={t.previous_week}
                    className="border-od-border-7 bg-od-panel-deep-3 text-od-muted hover:text-od-text-2 cursor-pointer rounded-s-[7px] border p-[8px_12px] hover:bg-[var(--od-raise-4)]"
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    onClick={() => setWeekOffset(0)}
                    className="border-od-border-7 bg-od-panel-deep-3 text-od-text-5 hover:text-od-text cursor-pointer border p-[8px_14px] text-[13px] hover:bg-[var(--od-raise-4)]"
                  >
                    {t.today}
                  </button>
                  <button
                    type="button"
                    onClick={() => setWeekOffset((value) => value + 1)}
                    aria-label={t.next_week}
                    className="border-od-border-7 bg-od-panel-deep-3 text-od-muted hover:text-od-text-2 cursor-pointer rounded-e-[7px] border p-[8px_12px] hover:bg-[var(--od-raise-4)]"
                  >
                    ›
                  </button>
                </div>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-start gap-[22px]">
              <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,560px)] flex-[4_1_620px] overflow-hidden rounded-[10px] border">
                <div
                  className="border-od-line bg-od-canvas-2 grid border-b"
                  style={{ gridTemplateColumns: GRID_COLUMNS }}
                >
                  <div className="border-od-line border-e p-[10px_8px]" />
                  {days.map((day, index) => (
                    <div
                      key={isoDay(day)}
                      className={`p-[10px_12px] ${index < DAYS_SHOWN - 1 ? "border-od-line border-e" : ""}`}
                      style={{
                        background: isoDay(day) === today ? "var(--od-raise)" : "transparent",
                      }}
                    >
                      <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
                        {weekdayName.format(day)}
                      </div>
                      <div
                        className="mt-1 text-[20px] font-semibold tracking-[-0.01em]"
                        style={{
                          color: isoDay(day) === today ? "var(--od-text)" : "var(--od-text-5)",
                        }}
                      >
                        {day.getDate()}
                      </div>
                      <div className="text-od-faint mt-[2px] text-[12px]">
                        {/* An unreachable calendar knows nothing - saying "free" would
                            be an invented availability, the one thing the calendar rule
                            forbids. */}
                        {state !== "ok"
                          ? "—"
                          : counts[index] === 0
                            ? t.day_free
                            : interpolate(t.day_busy, { count: counts[index] })}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="relative">
                  <div
                    className="relative grid"
                    style={{
                      gridTemplateColumns: GRID_COLUMNS,
                      gridTemplateRows: `repeat(${ROWS}, ${ROW_HEIGHT}px)`,
                    }}
                  >
                    {Array.from({ length: END_HOUR - START_HOUR }, (_, index) => {
                      const hour = START_HOUR + index;
                      return (
                        <div
                          key={hour}
                          dir="ltr"
                          className="mono border-od-line text-od-faint-2 border-e border-t border-t-[color:var(--od-raise-6)] p-[4px_8px] text-[11.5px]"
                          style={{ gridColumn: 1, gridRow: `${index * 4 + 1} / span 4` }}
                        >
                          {String(hour).padStart(2, "0")}:00
                        </div>
                      );
                    })}

                    {Array.from({ length: Math.ceil(ROWS / 2) }, (_, rowIndex) =>
                      days.map((day, columnIndex) => {
                        const row = rowIndex * 2;
                        const hourLine = row % 4 === 0;
                        return (
                          <div
                            key={`${row}-${columnIndex}`}
                            style={{
                              gridColumn: columnIndex + 2,
                              gridRow: `${row + 1} / span 2`,
                              borderTop: `1px solid ${hourLine ? "var(--od-raise-6)" : "var(--od-raise)"}`,
                              borderInlineEnd:
                                columnIndex < DAYS_SHOWN - 1 ? "1px solid var(--od-line)" : "none",
                              background:
                                isoDay(day) === today ? "rgba(255,255,255,.012)" : "transparent",
                            }}
                          />
                        );
                      }),
                    )}

                    {days.map((day, columnIndex) =>
                      busy.map((period, index) => {
                        const placed = rowsOnDay(period.start, period.end, day);
                        if (placed === null) return null;
                        return (
                          <div
                            key={`${columnIndex}-${index}`}
                            className="z-[2] m-[2px_3px] overflow-hidden rounded-md border p-[4px_7px]"
                            style={{
                              gridColumn: columnIndex + 2,
                              gridRow: `${Math.floor(placed.top) + 1} / span ${Math.max(1, Math.round(placed.span))}`,
                              marginTop: (placed.top % 1) * ROW_HEIGHT,
                              borderColor: "var(--od-border-9)",
                              background: "var(--od-raise-5)",
                            }}
                          >
                            <span className="text-od-text-2 block overflow-hidden text-[12.5px] leading-[1.25] font-semibold text-ellipsis whitespace-nowrap">
                              {t.busy_label}
                            </span>
                            <span
                              dir="ltr"
                              className="mono ltr-data text-od-muted-4 mt-px block overflow-hidden text-[11.5px] leading-[1.2] text-ellipsis whitespace-nowrap"
                            >
                              {timeLabel.format(period.start)}–{timeLabel.format(period.end)}
                            </span>
                          </div>
                        );
                      }),
                    )}
                  </div>
                </div>
              </div>

              <div className="flex max-w-[380px] min-w-[min(100%,290px)] flex-[1_1_300px] flex-col gap-4">
                <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-4">
                  <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                    {t.hours_heading}
                  </div>
                  {data?.hours ? (
                    <div dir="ltr" className="mono ltr-data text-od-muted mt-[10px] text-start text-[12.5px]">
                      {data.hours}
                      {data.timezone ? ` · ${data.timezone}` : ""}
                    </div>
                  ) : (
                    <div className="text-od-muted-5 mt-[10px] text-[13px] text-pretty">
                      {t.hours_unset}
                    </div>
                  )}
                  <div className="text-od-faint mt-3 text-[12.5px] text-pretty">{t.hours_footer}</div>
                  <Link
                    href={`/${locale}/rules`}
                    className="text-od-violet mt-[10px] inline-block text-[13px] hover:underline"
                  >
                    {t.edit_in_rules}
                  </Link>
                </div>

                <div
                  className="rounded-[10px] border p-4"
                  style={{
                    borderColor:
                      state === "unreachable" ? "var(--od-red-border-3)" : "var(--od-line)",
                    background:
                      state === "unreachable" ? "var(--od-red-bg-4)" : "var(--od-panel-deep-3)",
                  }}
                >
                  <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                    {t.sync_heading}
                  </div>
                  <div
                    dir="ltr"
                    className="mono ltr-data text-od-muted-2 mt-[10px] text-start text-[12.5px] [overflow-wrap:anywhere]"
                  >
                    {data?.source ?? ""} · CalDAV
                  </div>
                  <div
                    className="mt-2 text-[13px] text-pretty"
                    style={{
                      color:
                        state === "unreachable" ? "var(--od-red-text-6)" : "var(--od-muted-5)",
                    }}
                  >
                    {state === "unreachable" ? t.sync_offline : t.sync_ok}
                  </div>
                  <Link
                    href={`/${locale}/settings`}
                    className="text-od-violet mt-[10px] inline-block text-[13px] hover:underline"
                  >
                    {t.edit_in_settings}
                  </Link>
                </div>

                <div className="border-od-line bg-od-panel-deep-2 rounded-[10px] border p-4">
                  <div className="text-od-faint text-[12.5px] text-pretty">{t.readonly_note}</div>
                </div>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function NotConnected({ locale, t }: { locale: Locale; t: CalendarDictionary }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-6 bg-od-panel-deep-2 max-w-[460px] rounded-[10px] border border-dashed p-[26px] text-center">
        <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
        <p className="text-od-muted mt-[10px] text-pretty">{t.empty_body}</p>
        <div className="mt-4 flex flex-wrap justify-center gap-[10px]">
          <Link
            href={`/${locale}/settings`}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 inline-block cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            {t.empty_goto}
          </Link>
        </div>
      </div>
    </div>
  );
}

function CredentialsRejected({
  locale,
  source,
  t,
}: {
  locale: Locale;
  source: string | null | undefined;
  t: CalendarDictionary;
}) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.error_label}
        </div>
        <h2 className="mt-[18px] mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">{t.error_body}</p>
        <div className="mt-5 flex flex-wrap gap-[10px]">
          <Link
            href={`/${locale}/settings`}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 inline-block cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            {t.error_update}
          </Link>
        </div>
        {source ? (
          <div
            dir="ltr"
            className="border-od-border mono ltr-data text-od-faint mt-[18px] flex flex-wrap gap-4 border-t pt-[14px] text-[11.5px]"
          >
            <span>caldav/401</span>
            <span className="[overflow-wrap:anywhere]">{source}</span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function FetchFailed({
  message,
  retry,
  t,
}: {
  message: string;
  retry: () => void;
  t: CalendarDictionary;
}) {
  return (
    <div className="border-od-red-border bg-od-red-bg mb-4 flex flex-wrap items-center justify-between gap-3 rounded-[10px] border p-[12px_16px]">
      <span className="text-[color:var(--od-red-text-2)]">{message}</span>
      <button
        type="button"
        onClick={retry}
        className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[7px_13px] text-[13px] font-medium text-[color:var(--od-red-text-3)]"
      >
        {t.offline_retry}
      </button>
    </div>
  );
}

function CalendarSkeleton() {
  const shimmer = {
    background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  };

  return (
    <div>
      <div
        className="h-7 w-[200px] rounded-md"
        style={{
          background: "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
          backgroundSize: "420px 100%",
          animation: "od-shimmer 1.4s linear infinite",
        }}
      />
      <div className="mt-[22px] flex flex-wrap gap-[22px]">
        <div className="border-od-raise-12 flex-[4_1_620px] overflow-hidden rounded-[10px] border">
          <div
            className="border-od-raise-12 grid border-b"
            style={{ gridTemplateColumns: GRID_COLUMNS }}
          >
            {[0, 1, 2, 3, 4, 5, 6, 7].map((index) => (
              <div key={index} className="border-od-raise-12 h-[58px] border-e" style={shimmer} />
            ))}
          </div>
          {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((index) => (
            <div
              key={index}
              className="h-11 border-t border-[color:var(--od-raise-6)]"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-panel-deep-3),var(--od-raise-3),var(--od-panel-deep-3))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
          ))}
        </div>
        <div className="flex flex-[1_1_300px] flex-col gap-4">
          {[190, 150].map((height) => (
            <div
              key={height}
              className="border-od-raise-12 rounded-[10px] border"
              style={{ height, ...shimmer }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
