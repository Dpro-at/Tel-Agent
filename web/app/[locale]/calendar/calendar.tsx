"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { CalendarDictionary } from "./page";

type Key = keyof CalendarDictionary;

const START_HOUR = 8;
const END_HOUR = 19;
const SLOT_MINUTES = 15;
const ROW_HEIGHT = 26;
const ROWS = ((END_HOUR - START_HOUR) * 60) / SLOT_MINUTES;
const GRID_COLUMNS = "64px repeat(6, minmax(0,1fr))";
const TODAY = 3;

/** The weekday is a word; the date number is the same in every language. */
const DAYS: { name: Key; num: string }[] = [
  { name: "day_mon", num: "17" },
  { name: "day_tue", num: "18" },
  { name: "day_wed", num: "19" },
  { name: "day_thu", num: "20" },
  { name: "day_fri", num: "21" },
  { name: "day_sat", num: "22" },
];

/** How an appointment got into the calendar decides how it reads. */
const KINDS = {
  normal: {
    border: "var(--od-border-9)",
    background: "var(--od-raise-5)",
    title: "var(--od-text-2)",
    meta: "var(--od-muted-4)",
  },
  agent: {
    border: "var(--od-violet-border)",
    background: "rgba(139,124,255,.13)",
    title: "var(--od-violet-4)",
    meta: "var(--od-violet-2)",
  },
  pending: {
    border: "var(--od-amber-border)",
    background: "var(--od-amber-bg)",
    title: "var(--od-amber-text-2)",
    meta: "var(--od-amber-text-3)",
  },
  cancelled: {
    border: "var(--od-red-border)",
    background: "var(--od-red-bg-4)",
    title: "var(--od-red-text-6)",
    meta: "var(--od-red-text-7)",
  },
  internal: {
    border: "var(--od-border-4)",
    background: "var(--od-panel-deep-7)",
    title: "var(--od-muted)",
    meta: "var(--od-faint)",
  },
} as const;

type Kind = keyof typeof KINDS;

/**
 * A customer's name is data; what the appointment is for is copy. Business time -
 * lunch, home visits, the open surgery - has no customer, so its title is copy too.
 */
type Event = {
  day: number;
  at: string;
  minutes: number;
  name?: string;
  nameKey?: Key;
  detail: Key;
  kind: Kind;
};

const EVENTS: Event[] = [
  { day: 0, at: "08:00", minutes: 60, name: "Josef Hofer", detail: "ev_consultation", kind: "normal" },
  { day: 0, at: "09:30", minutes: 45, name: "Elisabeth Mayr", detail: "ev_followup_sabine", kind: "normal" },
  { day: 0, at: "11:00", minutes: 30, nameKey: "ev_planning_name", detail: "ev_planning", kind: "internal" },
  { day: 0, at: "14:00", minutes: 60, name: "Markus Steiner", detail: "ev_sports_georg", kind: "agent" },
  { day: 1, at: "08:30", minutes: 45, name: "Karin Bauer", detail: "ev_back_sabine", kind: "normal" },
  { day: 1, at: "10:00", minutes: 60, name: "Anna Gruber", detail: "ev_followup_georg", kind: "cancelled" },
  { day: 1, at: "13:00", minutes: 45, name: "Peter Nowak", detail: "ev_shoulder", kind: "normal" },
  { day: 1, at: "15:30", minutes: 60, name: "Ingrid Lechner", detail: "ev_followup_georg", kind: "agent" },
  { day: 2, at: "09:00", minutes: 90, nameKey: "ev_homevisits_name", detail: "ev_homevisits", kind: "internal" },
  { day: 2, at: "13:30", minutes: 45, name: "Franz Huber", detail: "ev_knee", kind: "normal" },
  { day: 2, at: "16:00", minutes: 45, name: "Julia Berger", detail: "ev_neck", kind: "pending" },
  { day: 3, at: "10:00", minutes: 60, name: "Anna Gruber", detail: "ev_change", kind: "agent" },
  { day: 3, at: "12:00", minutes: 30, nameKey: "ev_lunch_name", detail: "ev_lunch", kind: "internal" },
  { day: 3, at: "14:30", minutes: 45, name: "Stefan Reiter", detail: "ev_postop", kind: "normal" },
  { day: 4, at: "08:00", minutes: 45, name: "Maria Fischer", detail: "ev_back_georg", kind: "normal" },
  { day: 4, at: "11:30", minutes: 60, name: "Thomas Brandl", detail: "ev_sports_sabine", kind: "normal" },
  { day: 4, at: "15:00", minutes: 45, name: "Eva Wimmer", detail: "ev_followup_georg", kind: "pending" },
  { day: 5, at: "09:00", minutes: 60, nameKey: "ev_open_name", detail: "ev_open", kind: "internal" },
];

const AGENT_BOOKINGS: { name: string; detail: Key; when: Key; href: string }[] = [
  { name: "Anna Gruber", detail: "agent_gruber", when: "agent_when_thu", href: "/calls/1" },
  { name: "Ingrid Lechner", detail: "agent_lechner", when: "agent_when_tue", href: "/calls/2" },
  { name: "Markus Steiner", detail: "agent_steiner", when: "agent_when_mon", href: "/calls/3" },
];

const HOURS_RULES: { label: Key; value?: string; valueKey?: Key }[] = [
  { label: "hours_weekdays", value: "08:00 – 17:00" },
  { label: "hours_saturday", value: "09:00 – 12:00" },
  { label: "hours_shortest", valueKey: "hours_shortest_value" },
  { label: "hours_notice", valueKey: "hours_notice_value" },
];

const LEGEND: { label: Key; kind: Kind }[] = [
  { label: "legend_agent", kind: "agent" },
  { label: "legend_normal", kind: "normal" },
  { label: "legend_pending", kind: "pending" },
  { label: "legend_cancelled", kind: "cancelled" },
  { label: "legend_internal", kind: "internal" },
];

const SERVICES: { id: string; label: Key; minutes: number }[] = [
  { id: "consultation", label: "service_consultation", minutes: 60 },
  { id: "followup", label: "service_followup", minutes: 45 },
  { id: "first", label: "service_first", minutes: 30 },
  { id: "short", label: "service_short", minutes: 20 },
];
const SLOT_TIMES = ["08:20", "09:00", "11:15", "13:40", "15:00", "16:20"];

/** Two are people; "any free" is a choice the interface offers. */
const STAFF: { id: string; name?: string; labelKey?: Key }[] = [
  { id: "georg", name: "Georg Wagner" },
  { id: "sabine", name: "Sabine" },
  { id: "any", labelKey: "staff_any" },
];

const CONFIRM_WAYS: { id: string; name?: string; labelKey?: Key }[] = [
  { id: "whatsapp", name: "WhatsApp" },
  { id: "sms", name: "SMS" },
  { id: "none", labelKey: "confirm_nothing" },
];

function toRow(time: string): number {
  const [hours, minutes] = time.split(":").map(Number);
  return ((hours - START_HOUR) * 60 + minutes) / SLOT_MINUTES;
}

function addMinutes(time: string, minutes: number): string {
  const [h, m] = time.split(":").map(Number);
  const total = h * 60 + m + minutes;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(Math.floor(total / 60) % 24)}:${pad(total % 60)}`;
}

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

export function Calendar({ locale, t }: { locale: Locale; t: CalendarDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [mode, setMode] = useState<"day" | "week" | "month">("week");
  const [newOpen, setNewOpen] = useState(false);

  const offline = state === "offline";
  const empty = state === "empty";
  const showCalendar = state === "default" || empty || offline;

  const counts = DAYS.map(
    (_, index) =>
      EVENTS.filter(
        (event) => event.day === index && event.kind !== "internal" && event.kind !== "cancelled",
      ).length,
  );
  const nowRow = toRow("11:15");

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="calendar" />
      </div>

      <StatePreview state={state} onChange={setState} />

      {offline ? (
        <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-4">
          <span
            className="size-[10px] flex-none rounded-full bg-[#F0605E]"
            style={{ animation: "od-ring 1.6s ease-out infinite" }}
          />
          <div className="min-w-[240px] flex-[1_1_340px]">
            <div className="text-[16px] font-semibold text-[color:var(--od-red-text)]">
              {t.offline_title}
            </div>
            <div className="mt-[3px] text-[color:var(--od-red-text-2)]">
              {t.offline_body_before}
              <span className="mono">08:12</span>
              {t.offline_body_after}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[9px_15px] font-medium text-[color:var(--od-red-text-3)]"
            >
              {t.offline_retry}
            </button>
            <button
              type="button"
              className="hover:bg-od-red-bg-2 cursor-pointer rounded-md border border-[color:var(--od-red-border-4)] bg-transparent p-[9px_15px] text-[color:var(--od-red-text-2)]"
            >
              {t.offline_stop}
            </button>
          </div>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1560px] p-[22px_28px_70px]">
        {state === "error" ? <CredentialsRejected t={t} /> : null}
        {state === "loading" ? <CalendarSkeleton /> : null}

        {showCalendar ? (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div>
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                <div className="text-od-muted-4 mt-[5px]">{t.week}</div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="border-od-border-2 bg-od-panel-deep-3 flex gap-[2px] rounded-lg border p-[3px]">
                  {(
                    [
                      { id: "day", label: t.mode_day },
                      { id: "week", label: t.mode_week },
                      { id: "month", label: t.mode_month },
                    ] as const
                  ).map((entry) => (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => setMode(entry.id)}
                      className={`cursor-pointer rounded-md border p-[6px_12px] text-[13px] whitespace-nowrap ${
                        mode === entry.id
                          ? "border-od-stroke bg-od-line-2 text-od-text"
                          : "text-od-muted-4 border-transparent bg-transparent"
                      }`}
                    >
                      {entry.label}
                    </button>
                  ))}
                </div>
                <div className="flex gap-[2px]">
                  <button
                    type="button"
                    className="border-od-border-7 bg-od-panel-deep-3 text-od-muted hover:text-od-text-2 cursor-pointer rounded-s-[7px] border p-[8px_12px] hover:bg-[var(--od-raise-4)]"
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    className="border-od-border-7 bg-od-panel-deep-3 text-od-text-5 hover:text-od-text cursor-pointer border p-[8px_14px] text-[13px] hover:bg-[var(--od-raise-4)]"
                  >
                    {t.today}
                  </button>
                  <button
                    type="button"
                    className="border-od-border-7 bg-od-panel-deep-3 text-od-muted hover:text-od-text-2 cursor-pointer rounded-e-[7px] border p-[8px_12px] hover:bg-[var(--od-raise-4)]"
                  >
                    ›
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => setNewOpen(true)}
                  className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] font-medium"
                >
                  {t.new_appointment}
                </button>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-start gap-[22px]">
              <div className="border-od-line bg-od-panel-deep-3 min-w-[min(100%,560px)] flex-[4_1_620px] overflow-hidden rounded-[10px] border">
                <div
                  className="border-od-line bg-od-canvas-2 grid border-b"
                  style={{ gridTemplateColumns: GRID_COLUMNS }}
                >
                  <div className="border-od-line border-e p-[10px_8px]" />
                  {DAYS.map((day, index) => (
                    <div
                      key={day.num}
                      className={`p-[10px_12px] ${index < 5 ? "border-od-line border-e" : ""}`}
                      style={{ background: index === TODAY ? "var(--od-raise)" : "transparent" }}
                    >
                      <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
                        {t[day.name]}
                      </div>
                      <div
                        className="mt-1 text-[20px] font-semibold tracking-[-0.01em]"
                        style={{ color: index === TODAY ? "var(--od-text)" : "var(--od-text-5)" }}
                      >
                        {day.num}
                      </div>
                      <div className="text-od-faint mt-[2px] text-[12px]">
                        {empty
                          ? t.day_closed
                          : interpolate(t.day_booked, { count: counts[index] })}
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
                      DAYS.map((_, columnIndex) => {
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
                                columnIndex < 5 ? "1px solid var(--od-line)" : "none",
                              background:
                                columnIndex === TODAY ? "rgba(255,255,255,.012)" : "transparent",
                            }}
                          />
                        );
                      }),
                    )}

                    {!empty
                      ? EVENTS.map((event, index) => {
                          const tone = KINDS[event.kind];
                          return (
                            <div
                              key={index}
                              className="z-[2] m-[2px_3px] cursor-pointer overflow-hidden rounded-md border p-[4px_7px]"
                              style={{
                                gridColumn: event.day + 2,
                                gridRow: `${toRow(event.at) + 1} / span ${Math.max(1, event.minutes / SLOT_MINUTES)}`,
                                borderColor: tone.border,
                                background: tone.background,
                              }}
                            >
                              <div className="flex min-w-0 items-center gap-[6px]">
                                {event.kind === "agent" ? (
                                  <span className="size-[6px] flex-none rounded-full bg-[color:var(--od-violet)]" />
                                ) : null}
                                <span
                                  className="min-w-0 overflow-hidden text-[12.5px] leading-[1.25] font-semibold text-ellipsis whitespace-nowrap"
                                  style={{
                                    color: tone.title,
                                    textDecoration:
                                      event.kind === "cancelled" ? "line-through" : "none",
                                  }}
                                >
                                  {event.nameKey ? t[event.nameKey] : event.name}
                                </span>
                              </div>
                              <div
                                className="mt-px overflow-hidden text-[11.5px] leading-[1.2] text-ellipsis whitespace-nowrap"
                                style={{ color: tone.meta }}
                              >
                                {event.kind === "agent"
                                  ? `${t.legend_agent} · ${t[event.detail]}`
                                  : t[event.detail]}
                              </div>
                            </div>
                          );
                        })
                      : null}

                    {!empty ? (
                      <div
                        className="pointer-events-none relative z-[3] border-t-2 border-[#F0605E]"
                        style={{
                          gridColumn: "2 / -1",
                          gridRow: Math.floor(nowRow) + 1,
                          marginTop: (nowRow % 1) * ROW_HEIGHT,
                        }}
                      >
                        <span className="absolute -top-[5px] start-[-4px] size-2 rounded-full bg-[#F0605E]" />
                      </div>
                    ) : null}
                  </div>

                  {empty ? (
                    <div
                      className="absolute inset-0 flex items-start justify-center p-[40px_30px]"
                      style={{ background: "var(--od-scrim-2)" }}
                    >
                      <div className="border-od-border-6 bg-od-panel-deep-2 max-w-[460px] rounded-[10px] border border-dashed p-[26px] text-center">
                        <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
                        <p className="text-od-muted mt-[10px] text-pretty">{t.empty_body}</p>
                        <div className="mt-4 flex flex-wrap justify-center gap-[10px]">
                          <button
                            type="button"
                            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
                          >
                            {t.empty_goto}
                          </button>
                          <button
                            type="button"
                            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_16px]"
                          >
                            {t.empty_edit}
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="flex max-w-[380px] min-w-[min(100%,290px)] flex-[1_1_300px] flex-col gap-4">
                <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-4">
                  <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                    {t.agent_heading}
                  </div>
                  {empty ? (
                    <div className="text-od-muted-5 mt-[10px] text-[13px] text-pretty">
                      {t.agent_empty}
                    </div>
                  ) : (
                    <div className="mt-2 flex flex-col">
                      {AGENT_BOOKINGS.map((booking) => (
                        <div
                          key={booking.name}
                          className="border-od-border grid items-start gap-3 border-b py-[11px]"
                          style={{ gridTemplateColumns: "minmax(0,1fr) max-content" }}
                        >
                          <div className="min-w-0">
                            <div className="text-od-text-3 font-medium text-pretty">{booking.name}</div>
                            <div className="text-od-muted-5 mt-[3px] text-[12.5px]">
                              {t[booking.detail]}
                            </div>
                            <Link
                              href={`/${locale}${booking.href}`}
                              className="text-od-violet mt-[5px] inline-block text-[12.5px] hover:underline"
                            >
                              {t.open_the_call}
                            </Link>
                          </div>
                          <span className="text-od-muted text-[12.5px]">{t[booking.when]}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-4">
                  <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                    {t.hours_heading}
                  </div>
                  <div className="mt-3 flex flex-col gap-[9px]">
                    {HOURS_RULES.map((rule) => (
                      <div
                        key={rule.label}
                        className="flex flex-wrap items-baseline justify-between gap-x-[14px] gap-y-2"
                      >
                        <span className="text-od-text-5">{t[rule.label]}</span>
                        {rule.valueKey ? (
                          <span className="text-od-muted text-[12.5px]">{t[rule.valueKey]}</span>
                        ) : (
                          <span dir="ltr" className="mono ltr-data text-od-muted text-[12.5px]">
                            {rule.value}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                  <div className="text-od-faint mt-3 text-[12.5px] text-pretty">
                    {t.hours_footer}
                  </div>
                  <Link
                    href={`/${locale}/settings`}
                    className="text-od-violet mt-[10px] inline-block text-[13px] hover:underline"
                  >
                    {t.edit_in_settings}
                  </Link>
                </div>

                <div
                  className="rounded-[10px] border p-4"
                  style={{
                    borderColor: offline ? "var(--od-red-border-3)" : "var(--od-line)",
                    background: offline ? "var(--od-red-bg-4)" : "var(--od-panel-deep-3)",
                  }}
                >
                  <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                    {t.sync_heading}
                  </div>
                  <div
                    dir="ltr"
                    className="mono ltr-data text-od-muted-2 mt-[10px] text-start text-[12.5px] [overflow-wrap:anywhere]"
                  >
                    wagner-partner.at/dav · CalDAV
                  </div>
                  <div
                    className="mt-2 text-[13px] text-pretty"
                    style={{ color: offline ? "var(--od-red-text-6)" : "var(--od-muted-5)" }}
                  >
                    {offline ? t.sync_offline : t.sync_ok}
                  </div>
                </div>
              </div>
            </div>

            <div className="border-od-line bg-od-panel-deep-2 mt-[18px] flex flex-wrap items-center gap-x-[22px] gap-y-[14px] rounded-[10px] border p-[12px_16px]">
              <span className="text-od-faint text-[12px] tracking-[.07em] uppercase">
                {t.legend}
              </span>
              {LEGEND.map((entry) => {
                const tone = KINDS[entry.kind];
                return (
                  <span
                    key={entry.label}
                    className="text-od-muted inline-flex items-center gap-2 text-[13px]"
                  >
                    <span
                      className="size-3 rounded-[3px] border"
                      style={{ borderColor: tone.border, background: tone.background }}
                    />
                    <span>{t[entry.label]}</span>
                  </span>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>

      {newOpen ? <NewAppointmentDialog t={t} onClose={() => setNewOpen(false)} /> : null}
    </div>
  );
}

function NewAppointmentDialog({
  t,
  onClose,
}: {
  t: CalendarDictionary;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [service, setService] = useState(SERVICES[0]);
  const [slot, setSlot] = useState("09:00");
  const [staff, setStaff] = useState(STAFF[0]);
  const [confirmWay, setConfirmWay] = useState(CONFIRM_WAYS[0]);

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center overflow-auto p-[40px_20px]"
      style={{ background: "var(--od-scrim)" }}
    >
      <div
        className="border-od-border-9 bg-od-panel w-full max-w-[600px] overflow-hidden rounded-[14px] border"
        style={{ boxShadow: "0 26px 70px var(--od-scrim-3)" }}
      >
        <div className="border-od-border flex items-start justify-between gap-4 border-b p-[20px_24px_16px]">
          <div>
            <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.dialog_title}</h2>
            <div className="text-od-muted-4 mt-1 text-[13px]">
              Thursday 20 August · booked by you, not the assistant.
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

        <div className="p-[20px_24px]">
          <label className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium">
            {t.form_customer}
          </label>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Anna Gruber"
            className="border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-full rounded-lg border p-[10px_13px] text-[15px] outline-none"
          />
          <div className="text-od-faint mt-[6px] text-[12.5px] text-pretty">
            {name.length > 1 ? t.form_customer_matched : t.form_customer_hint}
          </div>

          <div className="mt-[18px]">
            <div className="text-od-text-5 mb-[7px] text-[12.5px] font-medium">{t.form_service}</div>
            <div className="flex flex-wrap gap-[7px]">
              {SERVICES.map((entry) => (
                <Chip
                  key={entry.id}
                  label={`${t[entry.label]} · ${entry.minutes} ${t.minutes}`}
                  on={service.id === entry.id}
                  onClick={() => setService(entry)}
                />
              ))}
            </div>
          </div>

          <div className="mt-[18px]">
            <div className="text-od-text-5 mb-[7px] text-[12.5px] font-medium">{t.form_slots}</div>
            <div className="flex flex-wrap gap-[7px]">
              {SLOT_TIMES.map((time) => (
                <Chip key={time} label={time} on={slot === time} onClick={() => setSlot(time)} />
              ))}
            </div>
            <div className="text-od-faint mt-2 text-[12.5px] text-pretty">{t.form_slots_hint}</div>
          </div>

          <div className="mt-[18px]">
            <div className="text-od-text-5 mb-[7px] text-[12.5px] font-medium">{t.form_with}</div>
            <div className="flex flex-wrap gap-[7px]">
              {STAFF.map((person) => (
                <Chip
                  key={person.id}
                  label={person.labelKey ? t[person.labelKey] : (person.name ?? "")}
                  on={staff.id === person.id}
                  onClick={() => setStaff(person)}
                />
              ))}
            </div>
          </div>

          <div className="border-od-border-4 bg-od-panel-deep-4 mt-5 rounded-[10px] border p-[14px_16px]">
            <div className="flex flex-wrap items-center justify-between gap-x-[14px] gap-y-[10px]">
              <div className="min-w-0">
                <div className="text-od-text-5 text-[12.5px] font-medium">{t.confirm_heading}</div>
                <div className="text-od-muted-5 mt-[3px] text-[12.5px] text-pretty">
                  {confirmWay.id === "none" ? t.confirm_none_note : t.confirm_sent_note}
                </div>
              </div>
              <div className="flex flex-wrap gap-[7px]">
                {CONFIRM_WAYS.map((way) => (
                  <Chip
                    key={way.id}
                    label={way.labelKey ? t[way.labelKey] : (way.name ?? "")}
                    on={confirmWay.id === way.id}
                    onClick={() => setConfirmWay(way)}
                  />
                ))}
              </div>
            </div>
          </div>

          <div className="mt-[18px] rounded-[10px] border border-[color:var(--od-violet-border)] bg-[var(--od-canvas-violet)] p-[14px_16px]">
            <div className="text-[11px] tracking-[.08em] uppercase text-[color:var(--od-violet-2)]">
              {t.summary}
            </div>
            <div className="text-od-text-3 mt-[6px] text-[14.5px] text-pretty">
              {interpolate(t.summary_line, {
                name: name || "Anna Gruber",
                service: t[service.label],
                from: slot,
                to: addMinutes(slot, service.minutes),
                staff: staff.labelKey ? t[staff.labelKey] : (staff.name ?? ""),
              })}
            </div>
          </div>
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
            {t.book_it}
          </button>
        </div>
      </div>
    </div>
  );
}

function CredentialsRejected({ t }: { t: CalendarDictionary }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.error_label}
        </div>
        <h2 className="mt-[18px] mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">{t.error_body}</p>
        <div className="mt-5 flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            {t.error_update}
          </button>
          <button
            type="button"
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_16px]"
          >
            {t.error_test}
          </button>
        </div>
        <div
          dir="ltr"
          className="border-od-border mono ltr-data text-od-faint mt-[18px] flex flex-wrap gap-4 border-t pt-[14px] text-[11.5px]"
        >
          <span>caldav/401</span>
          <span>wagner-partner.at/dav</span>
          <span>2026-08-16 11:04:22</span>
        </div>
      </div>
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
            {[0, 1, 2, 3, 4, 5, 6].map((index) => (
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
