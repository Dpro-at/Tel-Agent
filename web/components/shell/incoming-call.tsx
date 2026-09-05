"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import ar from "../../../locales/ar/incoming-call.json";
import de from "../../../locales/de/incoming-call.json";
import en from "../../../locales/en/incoming-call.json";
import es from "../../../locales/es/incoming-call.json";
import nl from "../../../locales/nl/incoming-call.json";

import { interpolate, pickDictionary } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

/**
 * Part of the shell, so it carries its own dictionary rather than being handed one
 * by every screen that can receive a call.
 */
type Dictionary = typeof en;
const DICTIONARIES = {
  en,
  de: de as Dictionary,
  ar: ar as Dictionary,
  es: es as Dictionary,
  nl: nl as Dictionary,
};
type Key = keyof Dictionary;

/** Seconds a person is given before the assistant picks up on its own. */
const GRACE = 6;

type Waiting = {
  id: string;
  name: string;
  number: string;
  note: Key;
  on: string;
  onLabel: Key;
  wait: string;
  ringing: boolean;
};

/** Names and numbers are data; the note and the line label are copy. */
const WAITING: Waiting[] = [
  {
    id: "a",
    name: "Anna Gruber",
    number: "+43 664 1234567",
    note: "note_customer",
    on: "+43 1 987 6543",
    onLabel: "line_main",
    wait: "0:04",
    ringing: true,
  },
  {
    id: "b",
    name: "+43 1 555 0182",
    number: "+43 1 555 0182",
    note: "note_unknown",
    on: "+43 1 987 6544",
    onLabel: "line_partners",
    wait: "0:11",
    ringing: true,
  },
  {
    id: "c",
    name: "Hoffmann GmbH",
    number: "+43 1 402 7781",
    note: "note_twice",
    on: "+43 1 987 6543",
    onLabel: "line_main",
    wait: "0:26",
    ringing: false,
  },
];

type Outcome = "ringing" | "agent" | "rejected" | "hidden";

export function IncomingCall({
  locale,
  enabled,
  waitingCount = 3,
  noPhoneRegistered = false,
  readOnlyRole = false,
}: {
  locale: Locale;
  enabled: boolean;
  waitingCount?: number;
  noPhoneRegistered?: boolean;
  readOnlyRole?: boolean;
}) {
  const t = pickDictionary<Dictionary>(locale, DICTIONARIES);
  const [outcome, setOutcome] = useState<Outcome>("ringing");
  const [seconds, setSeconds] = useState(0);
  const [frontId, setFrontId] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!enabled || outcome !== "ringing") return;
    timer.current = setInterval(() => {
      setSeconds((value) => {
        // Nobody picked up in time, so the assistant takes it.
        if (value + 1 >= GRACE) setOutcome("agent");
        return value + 1;
      });
    }, 1000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [enabled, outcome]);

  if (!enabled) return null;

  const waiting = WAITING.slice(0, Math.max(1, Math.min(3, waitingCount)));
  const front = waiting.find((call) => call.id === frontId) ?? waiting[0];
  const rest = waiting.filter((call) => call.id !== front.id);
  const canAnswer = !noPhoneRegistered && !readOnlyRole;
  const left = Math.max(0, GRACE - seconds);

  function ringAgain() {
    setSeconds(0);
    setOutcome("ringing");
  }

  if (outcome !== "ringing") {
    return (
      <button
        type="button"
        onClick={ringAgain}
        className="border-od-border-11 text-od-faint hover:text-od-text-2 hover:border-od-stroke fixed end-5 bottom-5 z-[80] inline-flex cursor-pointer items-center gap-2 rounded-full border border-dashed p-[7px_13px] text-[12px]"
        style={{ background: "var(--od-scrim-2)" }}
      >
        {outcome === "agent" ? t.took_call : outcome === "rejected" ? t.rejected : t.hidden}
      </button>
    );
  }

  return (
    <div
      className="bg-od-panel fixed end-5 bottom-5 z-[80] w-[336px] max-w-[calc(100vw-40px)] overflow-hidden rounded-[13px] border border-[color:var(--od-violet-border)]"
      style={{ boxShadow: "0 20px 50px var(--od-scrim-3)", animation: "od-rise .22s ease-out" }}
    >
      <div className="flex items-start gap-[10px] p-[14px_15px_12px]">
        <span
          className="mt-[5px] size-[9px] flex-none rounded-full bg-[color:var(--od-violet)]"
          style={{ animation: "od-ring-violet 1.4s ease-out infinite" }}
        />
        <div className="min-w-0 flex-[1_1_auto]">
          <div className="text-[11px] tracking-[.09em] uppercase text-[color:var(--od-violet-2)]">
            {waiting.length > 1
              ? interpolate(t.incoming_many, { count: waiting.length })
              : t.incoming_one}
          </div>
          <div className="text-od-text mt-1 text-[16px] font-semibold text-pretty">{front.name}</div>
          <div dir="ltr" className="mono ltr-data text-od-muted-5 mt-[2px] text-[12.5px]">
            {front.number}
          </div>
          <div className="text-od-faint mt-1 text-[12.5px] text-pretty">{t[front.note]}</div>
          <div className="text-od-muted-5 mt-[7px] flex flex-wrap items-center gap-x-2 gap-y-[5px] text-[11.5px]">
            <span dir="ltr" className="mono ltr-data">
              {front.on}
            </span>
            <span className="text-[color:var(--od-faint-5)]">·</span>
            <span className="text-pretty">{t[front.onLabel]}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setOutcome("hidden")}
          aria-label={t.hide}
          title={t.hide}
          className="border-od-border-2 text-od-muted-4 hover:bg-od-raise hover:text-od-text size-[26px] flex-none cursor-pointer rounded-[7px] border bg-transparent text-[14px] leading-none"
        >
          ×
        </button>
      </div>

      <div className="px-[15px] pb-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-[6px]">
          <span className="text-[12.5px] text-pretty text-[color:var(--od-muted-3)]">
            {left > 0 ? interpolate(t.answers_in, { seconds: left }) : t.answering}
          </span>
          <span className="text-od-text-3 text-[13px]">
            {interpolate(t.waiting_for, {
              elapsed: `0:${String(seconds).padStart(2, "0")}`,
            })}
          </span>
        </div>
        <div className="mt-[7px] h-1 overflow-hidden rounded-full bg-[var(--od-raise-3)]">
          <span
            className="block h-full rounded-full transition-[width] duration-[900ms] ease-linear"
            style={{
              width: `${Math.min(100, (seconds / GRACE) * 100)}%`,
              background: left <= 2 ? "var(--od-amber)" : "var(--od-violet)",
            }}
          />
        </div>
      </div>

      {rest.length > 0 ? (
        <div className="border-od-border border-t">
          <div className="p-[9px_15px_5px] text-[10.5px] tracking-[.09em] uppercase text-[color:var(--od-faint-5)]">
            {rest.length === 1
              ? t.also_waiting_one
              : interpolate(t.also_waiting_many, { count: rest.length })}
          </div>
          {rest.map((call) => (
            <button
              key={call.id}
              type="button"
              onClick={() => setFrontId(call.id)}
              className="hover:bg-od-raise flex w-full cursor-pointer items-center gap-[9px] border-none border-t border-[color:var(--od-raise-6)] bg-transparent p-[8px_15px]"
            >
              <span className="min-w-0 flex-[1_1_auto] text-start">
                <span className="text-od-text-2 block text-[13px] font-medium text-pretty">
                  {call.name}
                </span>
                <span
                  dir="ltr"
                  className="mono ltr-data text-od-faint mt-px block text-[11.5px]"
                >
                  {call.on}
                </span>
              </span>
              <span
                className="flex-none rounded-[5px] border p-[1px_7px] text-[10.5px] font-semibold whitespace-nowrap"
                style={{
                  borderColor: call.ringing ? "var(--od-border-2)" : "var(--od-violet-border)",
                  background: call.ringing ? "transparent" : "rgba(139,124,255,.13)",
                  color: call.ringing ? "var(--od-muted-4)" : "var(--od-violet-3)",
                }}
              >
                {call.ringing ? t.state_ringing : t.state_assistant}
              </span>
              <span dir="ltr" className="mono ltr-data text-od-muted-5 flex-none text-[12px]">
                {call.wait}
              </span>
            </button>
          ))}
        </div>
      ) : null}

      <div className="border-od-border bg-od-panel-deep-2 flex gap-[7px] border-t p-[12px_15px]">
        {canAnswer ? (
          <Link
            href={`/${locale}/live`}
            className="inline-flex flex-[1_1_0] items-center justify-center rounded-lg border border-[color:var(--od-green-border)] bg-[var(--od-panel-green)] p-[9px_10px] text-[13px] font-semibold text-[color:var(--od-green-text)] hover:bg-[var(--od-panel-green-3)] hover:no-underline"
          >
            {t.answer}
          </Link>
        ) : null}
        <button
          type="button"
          onClick={() => setOutcome("agent")}
          className="flex-[1_1_0] cursor-pointer rounded-lg border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.13)] p-[9px_10px] text-[13px] font-semibold text-[color:var(--od-violet-3)] hover:bg-[rgba(139,124,255,.2)]"
        >
          {t.assistant}
        </button>
        <button
          type="button"
          onClick={() => setOutcome("rejected")}
          className="border-od-red-border bg-od-red-bg hover:bg-od-red-bg-2 flex-none cursor-pointer rounded-lg border p-[9px_12px] text-[13px] font-semibold text-[color:var(--od-red-text-4)]"
        >
          {t.reject}
        </button>
      </div>

      {!canAnswer ? (
        <div className="border-od-border bg-od-amber-bg flex items-start gap-[9px] border-t p-[11px_15px]">
          <span className="mt-px flex-none text-[color:var(--od-amber)]">!</span>
          <div className="min-w-0 text-[12px] text-pretty text-[color:var(--od-amber-text-3)]">
            {noPhoneRegistered ? t.no_phone : t.read_only}
          </div>
        </div>
      ) : null}
    </div>
  );
}
