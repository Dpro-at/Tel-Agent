"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  OfflineError,
  attemptsLeft,
  recallResetUsername,
  verifyCode,
} from "@/lib/api";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import {
  AuthCard,
  AuthFrame,
  AuthSubmit,
  OfflineBanner,
  withMachineValue,
} from "../auth-frame";
import { INSTALLATION } from "../installation";

import type { CodeDictionary } from "./page";

const DIGITS = 6;
/** Codes last ten minutes; the countdown is cosmetic and the server is the judge. */
const CODE_LIFETIME_SECONDS = 10 * 60;

/**
 * Step two of three: the code itself.
 *
 * The same screen serves two-factor sign-in. The only difference is where the caller
 * came from and where they go next, so it is one screen with two entries rather than
 * two screens that drift apart.
 *
 * The six boxes are `dir="ltr"` in every language. A code has an order that exists
 * outside the interface - it is read aloud and typed left to right the same way a dial
 * pad reads 1-2-3 - so it must not mirror under RTL.
 */
export function Code({ locale, t }: { locale: Locale; t: CodeDictionary }) {
  const router = useRouter();
  const [state, setState] = useState<ScreenState>("default");
  const [digits, setDigits] = useState<string[]>(() => Array(DIGITS).fill(""));
  const boxes = useRef<(HTMLInputElement | null)[]>([]);

  const [busy, setBusy] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [wrongFromServer, setWrongFromServer] = useState(false);
  const [expiredFromServer, setExpiredFromServer] = useState(false);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(CODE_LIFETIME_SECONDS);

  // The username the code was sent to, remembered by the forgot screen. Arriving here
  // without one - a bookmark, a private window - means starting over, which costs one
  // click and reveals nothing. Read lazily: on the server there is no sessionStorage,
  // and the null it returns there matches the first client render, so no hydration
  // mismatch and no state write inside an effect.
  const [username] = useState<string | null>(() =>
    typeof window === "undefined" ? null : recallResetUsername(),
  );

  // Cosmetic countdown; the server enforces the real expiry.
  useEffect(() => {
    const timer = window.setInterval(
      () => setSecondsLeft((seconds) => Math.max(0, seconds - 1)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, []);

  const offline = state === "offline" || unreachable;
  const wrong = state === "error" || wrongFromServer;
  const expired = state === "stale" || expiredFromServer || secondsLeft === 0;
  const disabled = offline || expired || busy || username === null;
  const complete = digits.every((digit) => digit !== "");

  const countdown = `${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}`;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (disabled || !complete || username === null) return;

    setBusy(true);
    setUnreachable(false);
    setWrongFromServer(false);
    try {
      await verifyCode(username, digits.join(""), "reset");
      // The reset ticket is now an HttpOnly cookie; the next screen spends it.
      router.push(`/${locale}/login/new-password`);
    } catch (error) {
      if (error instanceof OfflineError) {
        setUnreachable(true);
      } else if (error instanceof ApiError && error.code === "code_wrong") {
        setWrongFromServer(true);
        setRemaining(attemptsLeft(error));
        setDigits(Array(DIGITS).fill(""));
        boxes.current[0]?.focus();
      } else if (error instanceof ApiError && error.code === "code_expired") {
        setExpiredFromServer(true);
      } else {
        setWrongFromServer(true);
      }
    } finally {
      setBusy(false);
    }
  }

  function write(index: number, value: string) {
    const typed = value.replace(/\D/g, "");
    if (!typed) return;

    setDigits((previous) => {
      const next = [...previous];
      // A paste fills the rest of the row from here; a keystroke fills one box.
      for (let offset = 0; offset < typed.length && index + offset < DIGITS; offset += 1) {
        next[index + offset] = typed[offset];
      }
      return next;
    });

    const landed = Math.min(index + typed.length, DIGITS - 1);
    boxes.current[landed]?.focus();
  }

  function onKeyDown(index: number, event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Backspace") {
      event.preventDefault();
      setDigits((previous) => {
        const next = [...previous];
        // Backspace clears this box, or steps back into the previous one if empty.
        if (next[index]) next[index] = "";
        else if (index > 0) next[index - 1] = "";
        return next;
      });
      if (!digits[index] && index > 0) boxes.current[index - 1]?.focus();
      return;
    }

    // The row is pinned LTR, so the arrow keys mean the same thing in Arabic.
    if (event.key === "ArrowLeft" && index > 0) boxes.current[index - 1]?.focus();
    if (event.key === "ArrowRight" && index < DIGITS - 1) boxes.current[index + 1]?.focus();
  }

  return (
    <AuthFrame
      banner={
        offline ? (
          <OfflineBanner
            title={t.offline_title}
            body={withMachineValue(
              t.offline_body,
              "host",
              `${INSTALLATION.host}:${INSTALLATION.port}`,
            )}
            retry={t.offline_retry}
          />
        ) : undefined
      }
      footer={
        <>
          <span className="text-od-muted-5">{t.self_hosted}</span>
          <Link href={`/${locale}/login`} className="text-od-muted-5 hover:underline">
            {t.back}
          </Link>
        </>
      }
    >
      <StatePreview
        state={state}
        onChange={setState}
        states={["default", "loading", "error", "stale", "offline"]}
        labels={{ stale: "Expired" }}
      />

      {state === "loading" ? (
        <LoadingCard />
      ) : (
        <AuthCard>
          <form onSubmit={submit}>
            <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
              {t.title}
            </h1>
            {/* The address is masked by the server; the screen does not know it, and
                showing the username instead would confirm the account exists. */}
            <p className="text-od-muted-4 mt-2 text-pretty">
              {withMachineValue(t.sent_to, "address", "•••")}
            </p>

            {username === null ? (
              <div className="border-od-amber-border bg-od-amber-bg mt-3 rounded-[10px] border p-[13px]">
                <div className="text-od-amber-text-2 text-[13px] text-pretty">
                  {t.resend_prompt_expired}{" "}
                  <Link
                    href={`/${locale}/login/forgot`}
                    className="text-od-violet hover:underline"
                  >
                    {t.resend}
                  </Link>
                </div>
              </div>
            ) : null}

            <div dir="ltr" className="mt-5 flex justify-between gap-[8px]">
              {digits.map((digit, index) => (
                <input
                  key={index}
                  ref={(element) => {
                    boxes.current[index] = element;
                  }}
                  value={digit}
                  onChange={(event) => write(index, event.target.value)}
                  onKeyDown={(event) => onKeyDown(index, event)}
                  inputMode="numeric"
                  autoComplete={index === 0 ? "one-time-code" : "off"}
                  maxLength={DIGITS}
                  disabled={disabled}
                  aria-label={interpolate(t.digit_label, { position: index + 1, total: DIGITS })}
                  className={[
                    "mono h-[54px] w-full min-w-0 rounded-lg border text-center text-[22px] outline-none",
                    "bg-od-canvas-2 text-od-text focus:border-od-violet",
                    wrong ? "border-od-red-border-2" : "border-od-border-6",
                    disabled ? "text-od-faint-2 cursor-not-allowed" : "",
                  ].join(" ")}
                />
              ))}
            </div>

            {wrong ? (
              <div className="text-od-red-text-4 mt-3 text-[13px] text-pretty" role="alert">
                {interpolate(t.wrong_code, { attempts: remaining ?? 0 })}
              </div>
            ) : null}

            {expired ? (
              <div className="border-od-amber-border bg-od-amber-bg mt-3 rounded-[10px] border p-[13px]">
                <div className="text-od-amber-text text-[13.5px] font-semibold text-pretty">
                  {t.expired_title}
                </div>
                <div className="text-od-amber-text-2 mt-1 text-[13px] text-pretty">
                  {t.expired_body}
                </div>
              </div>
            ) : null}

            <AuthSubmit disabled={disabled || !complete} className="mt-4">
              {offline ? t.submit_offline : t.submit}
            </AuthSubmit>

            <div className="border-od-border mt-[18px] flex flex-wrap items-center justify-between gap-x-4 gap-y-[10px] border-t pt-4">
              {expired ? (
                <span className="text-od-muted-5 text-[13px]">{t.resend_prompt_expired}</span>
              ) : (
                /* A countdown is a duration, not prose: LTR and monospace in every language. */
                <span className="text-od-muted-5 text-[13px]">
                  {withMachineValue(t.expires_in, "time", countdown)}
                </span>
              )}
              <Link
                href={`/${locale}/login/forgot`}
                className="text-od-violet hover:text-od-violet-2 text-[13px] hover:underline"
              >
                {t.resend}
              </Link>
            </div>
          </form>
        </AuthCard>
      )}
    </AuthFrame>
  );
}

/** The same shimmer the sign-in card uses, so the flow does not change texture mid-way. */
function LoadingCard() {
  const shimmer = (from: string, to: string) => ({
    background: `linear-gradient(90deg,var(${from}),var(${to}),var(${from}))`,
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  });

  return (
    <AuthCard>
      <div className="h-[22px] w-[58%] rounded-md" style={shimmer("--od-raise-2", "--od-raise-5")} />
      <div
        className="mt-3 h-[15px] w-[80%] rounded-md"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
      <div dir="ltr" className="mt-5 flex justify-between gap-[8px]">
        {Array.from({ length: DIGITS }).map((_, index) => (
          <div
            key={index}
            className="h-[54px] w-full rounded-lg"
            style={shimmer("--od-raise-2", "--od-raise-5")}
          />
        ))}
      </div>
      <div
        className="mt-4 h-[44px] w-full rounded-lg"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
    </AuthCard>
  );
}
