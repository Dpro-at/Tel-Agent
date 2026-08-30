"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { LiveDictionary } from "./page";

const CONTROL_PATHS: Record<string, string> = {
  whisper:
    "M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3Z M5.5 11a6.5 6.5 0 0 0 13 0 M12 17.5V21 M18 5.5c1.6 1.2 1.6 3.8 0 5",
  takeover: "M4 4h4l2 5-2.5 1.5a11 11 0 0 0 5.5 5.5L15 13.5l5 2V20a12 12 0 0 1-16-16Z M14.5 4.5h6 M17.5 1.5v6",
  handoff: "M3 20c0-2.8 2.4-4.5 5.5-4.5S14 17.2 14 20 M8.5 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z M16.5 9h5 M19 6.5 21.5 9 19 11.5",
  end: "M4 4h4l2 5-2.5 1.5a11 11 0 0 0 5.5 5.5L15 13.5l5 2V20a12 12 0 0 1-16-16Z M16 4l5 5 M21 4l-5 5",
  listen: "M4 15v-3a8 8 0 0 1 16 0v3 M4 14h3v6H5.5A1.5 1.5 0 0 1 4 18.5V14Z M20 14h-3v6h1.5A1.5 1.5 0 0 0 20 18.5V14Z",
};

function ControlIcon({ name }: { name: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={16}
      height={16}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {CONTROL_PATHS[name].split(" M").map((segment, index) => (
        <path key={index} d={(index ? "M" : "") + segment} />
      ))}
    </svg>
  );
}

type Key = keyof LiveDictionary;

/** A caller's name and number are data; what the call is and what to do are copy. */
type LiveCallEntry = {
  id: string;
  who?: string;
  whoKey?: Key;
  number: string;
  what: Key;
  elapsed: string;
  tone: "violet" | "amber";
  urgent?: Key;
  duty: Key;
};

const LIVE: LiveCallEntry[] = [
  {
    id: "gruber",
    who: "Anna Gruber",
    number: "+43 664 1234567",
    what: "live_gruber_what",
    elapsed: "0:32",
    tone: "violet",
    duty: "live_gruber_duty",
  },
  {
    id: "night",
    who: "+43 664 220 0910",
    number: "+43 664 220 0910",
    what: "live_night_what",
    elapsed: "1:04",
    tone: "violet",
    duty: "live_night_duty",
  },
  {
    id: "hoffmann",
    who: "Hoffmann GmbH",
    number: "+43 1 512 3390",
    what: "live_hoffmann_what",
    elapsed: "2:47",
    tone: "amber",
    urgent: "live_hoffmann_urgent",
    duty: "live_hoffmann_duty",
  },
];

/** The assistant speaks under its own name; "caller" is a role the interface names. */
const LINES: { t: string; speaker?: string; speakerKey?: Key; text: string }[] = [
  { t: "00:00", speaker: "Lena", text: "Wagner & Partner, good morning. This call is recorded. How can I help you?" },
  {
    t: "00:06",
    speakerKey: "speaker_caller",
    text: "Good morning, Gruber here. I have an appointment on Tuesday and I need to move it.",
  },
  {
    t: "00:13",
    speaker: "Lena",
    text: "Of course, Ms Gruber. I can see your appointment on Tuesday at 14:00. What day would suit you better?",
  },
  { t: "00:21", speakerKey: "speaker_caller", text: "Thursday would be better, in the morning if possible." },
  { t: "00:26", speaker: "Lena", text: "One moment, let me check the calendar." },
];

const KIND_STYLES = {
  in: { color: "var(--od-green-text)", border: "var(--od-green-border)", background: "rgba(63,185,132,.10)" },
  out: { color: "var(--od-muted-2)", border: "var(--od-border-9)", background: "var(--od-raise-5)" },
  missed: { color: "var(--od-amber-text)", border: "var(--od-amber-border)", background: "var(--od-amber-bg)" },
  blocked: { color: "var(--od-red-text-4)", border: "var(--od-red-border)", background: "rgba(240,96,94,.10)" },
} as const;

const RECENT: {
  id: string;
  name?: string;
  nameKey?: Key;
  number: string;
  direction: Key;
  when?: string;
  whenKey?: Key;
  kind: keyof typeof KIND_STYLES;
}[] = [
  { id: "r1", name: "Anna Gruber", number: "+43 664 1234567", direction: "dir_in", when: "09:41", kind: "in" },
  { id: "r2", nameKey: "caller_unknown", number: "+43 720 887 221", direction: "dir_blocked", when: "09:12", kind: "blocked" },
  { id: "r3", name: "Josef Hofer", number: "+43 699 5567 903", direction: "dir_in", when: "08:55", kind: "in" },
  { id: "r4", name: "Klara Wolf", number: "+43 1 512 3390", direction: "dir_out_mohamed", when: "08:31", kind: "out" },
  { id: "r5", name: "Elisabeth Mayr", number: "+43 1 402 8811", direction: "dir_missed", whenKey: "when_yesterday", kind: "missed" },
  { id: "r6", name: "Markus Steiner", number: "+43 650 771 4482", direction: "dir_out_reminder", whenKey: "when_yesterday", kind: "out" },
];

const DEVICES: { id: string; label: Key }[] = [
  { id: "desk", label: "device_desk" },
  { id: "dect", label: "device_dect" },
  { id: "computer", label: "device_computer" },
];

const KEYS: [string, string][] = [
  ["1", ""],
  ["2", "ABC"],
  ["3", "DEF"],
  ["4", "GHI"],
  ["5", "JKL"],
  ["6", "MNO"],
  ["7", "PQRS"],
  ["8", "TUV"],
  ["9", "WXYZ"],
  ["*", ""],
  ["0", "+"],
  ["#", ""],
];

type Confirm = "takeover" | "handoff" | "end" | null;

export function LiveCall({ locale, t }: { locale: Locale; t: LiveDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [seconds, setSeconds] = useState(32);
  const [active, setActive] = useState(0);
  const [ended, setEnded] = useState<number[]>([]);
  const [listening, setListening] = useState(false);
  const [whisperOpen, setWhisperOpen] = useState(false);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [mine, setMine] = useState(false);
  const [muted, setMuted] = useState(false);
  const [dialed, setDialed] = useState("");
  const [device, setDevice] = useState(DEVICES[0]);

  const offline = state === "offline";
  const empty = state === "empty";
  const idleView = state === "idle";
  const showPhone = state === "default" || empty || offline || idleView;

  const alive = LIVE.map((_, index) => index).filter((index) => !ended.includes(index));
  const hasLive = !empty && !idleView && alive.length > 0;
  const activeIndex = alive.includes(active) ? active : (alive[0] ?? 0);
  const open = LIVE[activeIndex] ?? LIVE[0];
  const inCall = hasLive;

  useEffect(() => {
    if (!inCall) return;
    const timer = setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [inCall]);

  const clock = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;

  const dutyLine = inCall ? t[open.duty] : offline ? t.duty_offline : t.duty_idle;

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="live" liveCalls={hasLive ? alive.length : 0} />
      </div>

      <StatePreview
        state={state}
        onChange={setState}
        states={["default", "idle", "empty", "loading", "error", "offline"]}
        labels={{ default: "Live" }}
      />

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
              <span className="mono">sip.easybell.de</span>
              {t.offline_body_middle}
              <span className="mono">09:58</span>
              {t.offline_body_after}
            </div>
          </div>
          <button
            type="button"
            className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[9px_15px] font-medium text-[color:var(--od-red-text-3)]"
          >
            {t.offline_retry}
          </button>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1400px] p-[22px_28px_70px]">
        {state === "error" ? <StreamLost locale={locale} /> : null}
        {state === "loading" ? <LiveSkeleton /> : null}

        {showPhone ? (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-[14px]">
              <div>
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                <div className="text-od-muted-4 mt-[5px] text-pretty">{dutyLine}</div>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-start gap-6">
              <div className="border-od-line bg-od-panel-deep-3 max-w-[320px] min-w-[min(100%,250px)] flex-[1_1_260px] overflow-hidden rounded-xl border">
                <div className="border-od-line bg-od-canvas-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-2 border-b p-[13px_15px]">
                  <span className="text-od-faint text-[12px] font-semibold tracking-[.08em] uppercase">
                    {t.on_the_line}
                  </span>
                  <span className="text-od-faint-2 text-[11.5px]">
                    {hasLive
                      ? alive.length === 1
                        ? t.calls_one
                        : interpolate(t.calls_many, { count: alive.length })
                      : t.calls_none}
                  </span>
                </div>

                {hasLive ? (
                  alive.map((index) => {
                    const call = LIVE[index];
                    const on = activeIndex === index;
                    return (
                      <div
                        key={call.id}
                        onClick={() => setActive(index)}
                        className="hover:bg-od-raise cursor-pointer border-b border-[color:var(--od-raise-6)] p-[12px_15px]"
                        style={{
                          borderInlineStart: `2px solid ${on ? "var(--od-violet)" : "transparent"}`,
                          background: on ? "var(--od-raise)" : "transparent",
                        }}
                      >
                        <div className="flex items-center gap-[9px]">
                          <span
                            className="size-2 flex-none rounded-full"
                            style={{
                              background:
                                call.tone === "amber" ? "var(--od-amber)" : "var(--od-violet)",
                              animation: on ? "od-ring-violet 1.8s ease-out infinite" : "none",
                            }}
                          />
                          <span className="text-od-text min-w-0 text-[13.5px] font-semibold text-pretty">
                            {call.whoKey ? t[call.whoKey] : call.who}
                          </span>
                          <span className="mono ltr-data text-od-muted-4 ms-auto text-[11.5px]">
                            {call.elapsed}
                          </span>
                        </div>
                        <div className="text-od-faint mt-[5px] text-[12px] text-pretty">
                          {t[call.what]}
                        </div>
                        {on ? (
                          <div className="mt-[6px] text-[11.5px] font-semibold text-[color:var(--od-violet-3)]">
                            {t.shown_right}
                          </div>
                        ) : null}
                        {call.urgent ? (
                          <div className="border-od-amber-border bg-od-amber-bg mt-[7px] rounded-md border p-[4px_9px] text-[11.5px] font-medium text-[color:var(--od-amber-text)]">
                            {t[call.urgent]}
                          </div>
                        ) : null}
                      </div>
                    );
                  })
                ) : (
                  <div className="text-od-muted-5 p-[26px_18px] text-[13px] text-pretty">
                    {t.nobody_live}
                  </div>
                )}
              </div>

              <div className="min-w-[min(100%,380px)] flex-[3_1_400px]">
                {inCall ? (
                  <div>
                    <div className="border-od-border flex flex-wrap items-start justify-between gap-x-[30px] gap-y-[18px] border-b pb-[18px]">
                      <div className="min-w-[240px] flex-[1_1_260px]">
                        <div className="border-od-red-border-3 bg-od-red-bg-4 inline-flex items-center gap-[9px] rounded-full border p-[5px_12px] text-[12.5px] font-bold tracking-[.08em] uppercase text-[color:var(--od-red-text-5)]">
                          <span
                            className="size-2 rounded-full bg-[#F0605E]"
                            style={{ animation: "od-live 1.1s ease-in-out infinite" }}
                          />
                          <span>Live</span>
                        </div>
                        <h2 className="text-od-text mt-3 mb-0 text-[26px] font-semibold tracking-[-0.015em]">
                          {open.who}
                        </h2>
                        <div className="mono ltr-data text-od-muted mt-[5px] text-[13.5px]">
                          {open.number}
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-x-8 gap-y-[22px]">
                        <div>
                          <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
                            {t.on_the_call}
                          </div>
                          <div className="mono ltr-data text-od-text mt-[5px] text-[20px]">{clock}</div>
                        </div>
                        <div>
                          <div className="text-od-faint text-[11px] tracking-[.08em] uppercase">
                            {t.speaking_now}
                          </div>
                          <div className="mt-2 flex items-center gap-[10px]">
                            <span className="inline-flex items-center gap-[7px] rounded-md border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.13)] p-[4px_11px] font-medium text-[color:var(--od-violet-3)]">
                              <span className="size-[6px] flex-none rounded-full bg-[color:var(--od-violet)]" />
                              <span>Lena</span>
                            </span>
                            <Meter />
                          </div>
                        </div>
                      </div>
                    </div>

                    <h3 className="text-od-muted-4 mt-[18px] mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
                      {t.transcript}
                    </h3>
                    <div className="flex flex-col">
                      {LINES.map((line) => (
                        <div
                          key={line.t}
                          className="grid gap-[18px] py-[9px]"
                          style={{ gridTemplateColumns: "52px minmax(78px, max-content) minmax(0,1fr)" }}
                        >
                          <span className="mono ltr-data text-od-faint-2 pt-[3px] text-[12.5px]">
                            {line.t}
                          </span>
                          <SpeakerLabel
                            speaker={line.speakerKey ? t[line.speakerKey] : (line.speaker ?? "")}
                            assistant={!line.speakerKey}
                          />
                          <span className="text-[16px] leading-[1.72] text-pretty text-[color:var(--od-text-4)]">
                            {line.text}
                          </span>
                        </div>
                      ))}

                      {/* The agent is mid-sentence: three dots rather than a half line. */}
                      <div
                        className="grid gap-[18px] py-[9px]"
                        style={{ gridTemplateColumns: "52px minmax(78px, max-content) minmax(0,1fr)" }}
                      >
                        <span className="mono ltr-data text-od-faint-2 pt-[3px] text-[12.5px]">
                          {clock}
                        </span>
                        <SpeakerLabel speaker="Lena" assistant />
                        <span className="inline-flex items-center gap-[6px] pt-[6px]">
                          {[0, 0.2, 0.4].map((delay) => (
                            <span
                              key={delay}
                              className="bg-od-faint size-[6px] rounded-full"
                              style={{ animation: `od-live 1.1s ease-in-out ${delay}s infinite` }}
                            />
                          ))}
                        </span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-[10px]">
                      <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
                        {t.recent}
                      </h2>
                      <Link href={`/${locale}/calls`} className="text-od-violet text-[13px] hover:underline">
                        {t.all_calls}
                      </Link>
                    </div>

                    {empty ? (
                      <div className="border-od-border-6 bg-od-panel-deep-2 rounded-[10px] border border-dashed p-[34px_26px]">
                        <h3 className="m-0 text-[18px] font-semibold">{t.no_calls_title}</h3>
                        <p className="text-od-muted mt-[10px] max-w-[56ch] text-pretty">{t.idle_note}</p>
                      </div>
                    ) : (
                      <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
                        {RECENT.map((entry) => {
                          const kind = KIND_STYLES[entry.kind];
                          return (
                            <div
                              key={entry.number + entry.when}
                              className="hover:bg-od-raise grid cursor-pointer items-center gap-[14px] border-b border-[color:var(--od-raise-6)] p-[11px_16px]"
                              style={{
                                gridTemplateColumns:
                                  "32px minmax(0,1fr) minmax(120px, max-content) max-content",
                              }}
                            >
                              <span className="border-od-border-9 text-od-text-5 inline-flex size-8 items-center justify-center rounded-full border bg-[var(--od-raise-5)] text-[13px] font-semibold">
                                {(entry.nameKey ? t[entry.nameKey] : (entry.name ?? "")).slice(0, 1)}
                              </span>
                              <div className="min-w-0">
                                <div className="text-od-text font-medium text-pretty">
                                  {entry.nameKey ? t[entry.nameKey] : entry.name}
                                </div>
                                <div className="mt-[2px] flex flex-wrap items-center gap-2">
                                  <span
                                    className="rounded-[5px] border p-[2px_8px] text-[11.5px] font-medium whitespace-nowrap"
                                    style={{
                                      borderColor: kind.border,
                                      background: kind.background,
                                      color: kind.color,
                                    }}
                                  >
                                    {t[entry.direction]}
                                  </span>
                                  <span className="mono ltr-data text-od-faint text-[12px]">
                                    {entry.number}
                                  </span>
                                </div>
                              </div>
                              <span className="text-od-muted-5 text-[12.5px]">
                                {entry.whenKey ? t[entry.whenKey] : entry.when}
                              </span>
                              <button
                                type="button"
                                className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_11px] text-[12.5px] hover:bg-[var(--od-raise-4)]"
                              >
                                {t.call_back}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="flex max-w-[400px] min-w-[min(100%,300px)] flex-[2_1_320px] flex-col gap-[14px]">
                <div className="border-od-line bg-od-panel-deep-3 rounded-xl border p-[18px]">
                  {inCall ? (
                    <div className="flex flex-col gap-[10px]">
                      <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                        {mine ? t.you_on_call : t.while_call_runs}
                      </div>

                      {mine ? (
                        <div>
                          <div
                            className="mt-1 max-w-[56ch] rounded-[10px] border p-[13px_15px] text-[12.5px] text-pretty"
                            style={{
                              borderColor: muted ? "var(--od-amber-border-2)" : "var(--od-green-border)",
                              background: muted ? "var(--od-amber-bg-2)" : "rgba(63,185,132,.06)",
                              color: muted ? "var(--od-amber-text-3)" : "var(--od-muted-2)",
                            }}
                          >
                            {muted
                              ? t.muted_note
                              : t.speaking_note}
                          </div>
                          <div className="mt-[10px] flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => setMuted((value) => !value)}
                              className="inline-flex flex-[1_1_auto] cursor-pointer items-center justify-center gap-2 rounded-lg border p-[11px_15px] text-[14px] font-semibold whitespace-nowrap"
                              style={{
                                borderColor: muted ? "var(--od-amber-border)" : "var(--od-stroke)",
                                background: muted ? "var(--od-amber-bg)" : "var(--od-raise-10)",
                                color: muted ? "var(--od-amber-text)" : "var(--od-text-2)",
                              }}
                            >
                              {muted ? "Unmute" : "Mute"}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setMine(false);
                                setMuted(false);
                              }}
                              className="inline-flex flex-[1_1_auto] cursor-pointer items-center justify-center gap-2 rounded-lg border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.10)] p-[11px_15px] text-[14px] font-semibold whitespace-nowrap text-[color:var(--od-violet-3)]"
                            >
                              {t.give_back}
                            </button>
                          </div>
                        </div>
                      ) : (
                        <>
                          {/* Listening in is silent for both sides, so the copy says so plainly. */}
                          <button
                            type="button"
                            onClick={() => setListening((value) => !value)}
                            className="w-full cursor-pointer rounded-[10px] border p-[14px_15px] text-start"
                            style={{
                              borderColor: listening ? "var(--od-violet-border)" : "var(--od-line)",
                              background: listening ? "rgba(139,124,255,.10)" : "var(--od-panel-deep-2)",
                            }}
                          >
                            <span className="flex flex-wrap items-center gap-[10px]">
                              <span
                                className="mt-[5px] size-[10px] flex-none rounded-full"
                                style={{
                                  background: listening ? "var(--od-violet)" : "var(--od-stroke-5)",
                                  animation: listening ? "od-ring-violet 1.6s ease-out infinite" : "none",
                                }}
                              />
                              <span className="text-od-muted-4 mt-[2px] inline-flex flex-none">
                                <ControlIcon name="listen" />
                              </span>
                              <span className="min-w-0 flex-[1_1_140px] text-start">
                                <span className="text-od-text block text-[16px] font-semibold">
                                  {listening ? t.listening_in : t.listen_in}
                                </span>
                                <span className="text-od-muted-2 mt-1 block text-[13px] text-pretty">
                                  {listening
                                    ? t.listen_on_note
                                    : t.listen_off_note}
                                </span>
                              </span>
                              {listening ? (
                                <span className="flex h-[18px] flex-none items-end gap-[2px]">
                                  {[11, 17, 8, 14, 6].map((height, index) => (
                                    <span
                                      key={index}
                                      className="block w-[3px] rounded-[2px] bg-[color:var(--od-violet)]"
                                      style={{ height }}
                                    />
                                  ))}
                                </span>
                              ) : null}
                            </span>
                          </button>

                          <div className="flex flex-wrap gap-2">
                            <ControlButton
                              tone="neutral"
                              glyph="whisper"
                              label={t.whisper}
                              title={t.whisper_title}
                              onClick={() => {
                                setWhisperOpen((value) => !value);
                                setConfirm(null);
                              }}
                            />
                            <ControlButton
                              tone="green"
                              glyph="takeover"
                              label={t.takeover}
                              title={t.takeover_title}
                              onClick={() => {
                                setConfirm("takeover");
                                setWhisperOpen(false);
                              }}
                            />
                            <ControlButton
                              tone="red"
                              glyph="handoff"
                              label={t.handoff}
                              title={t.handoff_title}
                              onClick={() => {
                                setConfirm("handoff");
                                setWhisperOpen(false);
                              }}
                            />
                          </div>
                        </>
                      )}

                      {confirm ? (
                        <ConfirmPanel
                          kind={confirm}
                          who={open.whoKey ? t[open.whoKey] : (open.who ?? "")}
                          othersLeft={Math.max(alive.length - 1, 0)}
                          t={t}
                          onCancel={() => setConfirm(null)}
                          onGo={() => {
                            if (confirm === "takeover") {
                              setMine(true);
                              setListening(false);
                            } else if (confirm === "end") {
                              setEnded((value) => [...value, activeIndex]);
                              setWhisperOpen(false);
                            }
                            setConfirm(null);
                          }}
                        />
                      ) : null}

                      {whisperOpen ? (
                        <div
                          className="border-od-stroke-3 rounded-[10px] border border-dashed p-[14px]"
                          style={{
                            background:
                              "repeating-linear-gradient(135deg, var(--od-panel-deep) 0 10px, var(--od-panel-deep-4) 10px 20px)",
                          }}
                        >
                          <div className="text-[12.5px] text-pretty text-[color:var(--od-muted-3)]">
                            {t.whisper_note}
                          </div>
                          <input
                            placeholder={t.whisper_placeholder}
                            className="border-od-stroke-4 bg-od-canvas-2 text-od-text-2 mt-[10px] w-full rounded-lg border border-dashed p-[10px_12px] outline-none"
                          />
                          <button
                            type="button"
                            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-2 w-full cursor-pointer rounded-lg border p-[10px] font-semibold"
                          >
                            {t.whisper_send}
                          </button>
                        </div>
                      ) : null}

                      <button
                        type="button"
                        onClick={() => {
                          setConfirm("end");
                          setWhisperOpen(false);
                        }}
                        className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 mt-1 inline-flex cursor-pointer items-center justify-center gap-[9px] rounded-[9px] border p-[13px] text-[15px] font-semibold whitespace-nowrap text-[color:var(--od-red-text-3)]"
                      >
                        <span className="inline-flex flex-none">
                          <ControlIcon name="end" />
                        </span>
                        <span>{t.end_call}</span>
                      </button>
                    </div>
                  ) : (
                    <div>
                      <div className="flex flex-wrap items-center justify-between gap-[10px]">
                        <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                          {t.dial}
                        </div>
                        <span className="mono ltr-data text-od-faint text-[12px]">
                          from +43 1 987 6543
                        </span>
                      </div>
                      <div
                        dir="ltr"
                        className="mono ltr-data border-od-border-6 bg-od-canvas-2 mt-3 min-h-[50px] rounded-[9px] border p-[12px_14px] text-[20px] tracking-[.04em] [overflow-wrap:anywhere]"
                        style={{ color: dialed ? "var(--od-text)" : "var(--od-faint-5)" }}
                      >
                        {dialed || "Enter a number"}
                      </div>
                      <div className="mt-[14px] grid grid-cols-3 gap-2">
                        {KEYS.map(([digit, letters]) => (
                          <button
                            key={digit}
                            type="button"
                            onClick={() => setDialed((value) => (value + digit).slice(0, 18))}
                            className="border-od-border-7 bg-od-canvas-2 hover:bg-od-raise cursor-pointer rounded-[9px] border p-[10px_0] text-center"
                          >
                            <span dir="ltr" className="text-od-text block text-[19px]">
                              {digit}
                            </span>
                            <span
                              dir="ltr"
                              className="text-od-faint-2 mt-px block text-[10px] tracking-[.12em]"
                            >
                              {letters}
                            </span>
                          </button>
                        ))}
                      </div>
                      <div className="mt-[14px] flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={offline}
                          onClick={() => {
                            setEnded([]);
                            setSeconds(0);
                            setState("default");
                          }}
                          className="flex-[1_1_160px] rounded-[9px] border p-[13px] text-[15px] font-semibold whitespace-normal"
                          style={{
                            cursor: offline ? "not-allowed" : "pointer",
                            borderColor: offline ? "var(--od-border-6)" : "var(--od-green-border)",
                            background: offline ? "var(--od-raise)" : "var(--od-panel-green-3)",
                            color: offline ? "var(--od-faint-2)" : "var(--od-green-text-2)",
                          }}
                        >
                          {t.call}
                        </button>
                        <button
                          type="button"
                          onClick={() => setDialed((value) => value.slice(0, -1))}
                          className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[9px] border bg-transparent p-[12px_14px] hover:bg-[var(--od-raise-4)]"
                        >
                          ⌫
                        </button>
                      </div>
                      <div className="text-od-faint mt-3 text-[12.5px] text-pretty">
                        {offline
                          ? t.dial_offline
                          : interpolate(t.dial_device, { device: t[device.label] })}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {DEVICES.map((entry) => (
                          <button
                            key={entry.id}
                            type="button"
                            onClick={() => setDevice(entry)}
                            className={`cursor-pointer rounded-full border p-[7px_12px] text-[12.5px] whitespace-nowrap ${
                              device.id === entry.id
                                ? "border-od-stroke bg-od-line-2 text-od-text"
                                : "border-od-border-7 bg-od-panel-deep-3 text-od-muted-4"
                            }`}
                          >
                            {t[entry.label]}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {inCall ? (
                  <div className="border-od-line bg-od-panel-deep-3 rounded-xl border p-4">
                    <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                      {t.tools_used}
                    </div>
                    <div className="mt-2 flex flex-col">
                      {[
                        { name: "search_knowledge", ms: "240 ms" },
                        { name: "check_calendar", ms: "890 ms" },
                      ].map((tool) => (
                        <div
                          key={tool.name}
                          className="border-od-border grid items-center gap-[10px] border-b py-[10px]"
                          style={{ gridTemplateColumns: "minmax(0,1fr) max-content" }}
                        >
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="size-[6px] flex-none rounded-full bg-[color:var(--od-green)]" />
                            <span className="mono ltr-data text-[12.5px] [overflow-wrap:anywhere] text-[color:var(--od-text-5)]">
                              {tool.name}
                            </span>
                          </div>
                          <span className="mono ltr-data text-od-faint text-[12px]">{tool.ms}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="border-od-line bg-od-panel-deep-3 rounded-xl border p-4">
                    <div className="text-od-muted-4 text-[12px] font-semibold tracking-[.07em] uppercase">
                      {t.task_heading}
                    </div>
                    <div className="text-od-muted-2 mt-2 text-[13px] text-pretty">
                      {t.task_note}
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_13px] text-[13px] font-medium"
                      >
                        {t.task_setup}
                      </button>
                      <button
                        type="button"
                        className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[8px_13px] text-[13px] hover:bg-[var(--od-raise-4)]"
                      >
                        {t.task_test}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** The assistant's own line is marked; the caller's is left plain. */
function SpeakerLabel({ speaker, assistant }: { speaker: string; assistant: boolean }) {
  const agent = assistant;
  return (
    <span
      className="mt-[2px] self-start rounded-[5px] border py-[2px] text-[12.5px] font-semibold whitespace-nowrap"
      style={{
        paddingInline: agent ? 9 : 0,
        borderColor: agent ? "rgba(139,124,255,.26)" : "transparent",
        background: agent ? "rgba(139,124,255,.09)" : "transparent",
        color: agent ? "var(--od-violet-3)" : "var(--od-muted-2)",
      }}
    >
      {speaker}
    </span>
  );
}

function Meter() {
  return (
    <span className="flex h-[18px] items-end gap-[3px]">
      {[10, 16, 7, 13, 5].map((height, index) => (
        <span
          key={index}
          className="w-[3px] rounded-[2px] bg-[color:var(--od-violet)]"
          style={{
            height,
            animation: `od-live ${0.7 + index * 0.13}s ease-in-out infinite`,
            animationDelay: `${index * 0.09}s`,
          }}
        />
      ))}
    </span>
  );
}

function ControlButton({
  tone,
  glyph,
  label,
  title,
  onClick,
}: {
  tone: "neutral" | "green" | "red";
  glyph: string;
  label: string;
  title: string;
  onClick: () => void;
}) {
  const styles = {
    neutral: { border: "var(--od-stroke)", background: "var(--od-raise-10)", color: "var(--od-text-2)" },
    green: {
      border: "var(--od-green-border)",
      background: "rgba(63,185,132,.11)",
      color: "var(--od-green-text)",
    },
    red: {
      border: "var(--od-red-border-2)",
      background: "var(--od-red-bg-2)",
      color: "var(--od-red-text-3)",
    },
  }[tone];

  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="inline-flex flex-[1_1_auto] cursor-pointer items-center justify-center gap-2 rounded-lg border p-[11px_15px] text-[14px] font-semibold whitespace-nowrap"
      style={{ borderColor: styles.border, background: styles.background, color: styles.color }}
    >
      <span className="inline-flex flex-none">
        <ControlIcon name={glyph} />
      </span>
      <span>{label}</span>
    </button>
  );
}

/**
 * Hanging up and taking over are both irreversible from the caller's side, so each
 * says exactly what the caller will experience before it happens.
 */
function ConfirmPanel({
  kind,
  who,
  othersLeft,
  t,
  onCancel,
  onGo,
}: {
  kind: Exclude<Confirm, null>;
  who: string;
  othersLeft: number;
  t: LiveDictionary;
  onCancel: () => void;
  onGo: () => void;
}) {
  const soft = kind === "takeover";

  // Each sentence is one key with placeholders, so a language orders it its own way.
  const title =
    kind === "takeover"
      ? t.confirm_takeover_title
      : kind === "end"
        ? interpolate(t.confirm_end_title, { who })
        : interpolate(t.confirm_handoff_title, { who });

  const body =
    kind === "takeover"
      ? t.confirm_takeover_body
      : kind === "end"
        ? interpolate(t.confirm_end_body, { count: othersLeft })
        : t.confirm_handoff_body;

  const goLabel =
    kind === "takeover"
      ? t.confirm_takeover_go
      : kind === "end"
        ? t.confirm_end_go
        : t.confirm_handoff_go;

  return (
    <div
      className="mt-1 rounded-[10px] border p-[14px_15px]"
      style={{
        borderColor: soft ? "var(--od-green-border)" : "var(--od-red-border-3)",
        background: soft ? "rgba(63,185,132,.06)" : "var(--od-red-bg-4)",
      }}
    >
      <div
        className="text-[15px] font-semibold text-pretty"
        style={{ color: soft ? "var(--od-green-text)" : "var(--od-red-text-3)" }}
      >
        {title}
      </div>
      <div
        className="mt-[5px] max-w-[56ch] text-[12.5px] text-pretty"
        style={{ color: soft ? "var(--od-muted-2)" : "var(--od-red-text-6)" }}
      >
        {body}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onGo}
          className="cursor-pointer rounded-[7px] border p-[9px_15px] text-[13px] font-semibold whitespace-nowrap"
          style={{
            borderColor: soft ? "var(--od-green-border)" : "var(--od-red-border-2)",
            background: soft ? "rgba(63,185,132,.14)" : "var(--od-red-bg-2)",
            color: soft ? "var(--od-green-text)" : "var(--od-red-text-3)",
          }}
        >
          {goLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_14px] text-[13px] whitespace-nowrap hover:bg-[var(--od-raise-4)]"
        >
          {t.cancel}
        </button>
      </div>
    </div>
  );
}

function StreamLost({ locale }: { locale: Locale }) {
  return (
    <div className="flex justify-center py-[70px]">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          Phone unavailable in this browser
        </div>
        <h2 className="mt-[18px] mb-0 text-[21px] font-semibold">
          This tab lost its connection to the call server
        </h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">
          The live stream to this page dropped, so you cannot dial or follow a call from here. Calls
          themselves are unaffected — the assistant is still answering. Reconnect, or pick calls up on a
          desk phone.
        </p>
        <div className="mt-5 flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            Reconnect
          </button>
          <Link
            href={`/${locale}/settings`}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 inline-block rounded-md border bg-transparent p-[9px_16px] hover:no-underline"
          >
            Check devices
          </Link>
        </div>
      </div>
    </div>
  );
}

function LiveSkeleton() {
  const shimmer = {
    background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  };

  return (
    <div className="flex flex-wrap gap-6">
      <div className="flex flex-[3_1_420px] flex-col gap-3">
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <div key={index} className="border-od-raise-12 h-[62px] rounded-[10px] border" style={shimmer} />
        ))}
      </div>
      <div className="border-od-raise-12 h-[460px] flex-[2_1_320px] rounded-xl border" style={shimmer} />
    </div>
  );
}
