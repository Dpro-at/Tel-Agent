"use client";

import Link from "next/link";
import { useState } from "react";

import { ApiError, OfflineError, forgetResetUsername, resetPassword } from "@/lib/api";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import {
  AuthCard,
  AuthFrame,
  AuthSubmit,
  OfflineBanner,
  AuthAction,
  authInputClass,
  withMachineValue,
} from "../auth-frame";
import { INSTALLATION } from "../installation";

import type { NewPasswordDictionary } from "./page";

/** The server enforces this too. The screen repeats it so nobody types twice to find out. */
const MIN_LENGTH = 12;

/**
 * Step three of three: choose the new password.
 *
 * The strength meter is computed here and nowhere else - it never leaves the browser,
 * which is the only honest way to score a password before it is set. It scores length
 * and variety, not a dictionary: a meter that calls a long passphrase weak because it
 * has no punctuation teaches the wrong lesson.
 */
function score(password: string): 0 | 1 | 2 | 3 | 4 {
  if (!password) return 0;

  let points = 0;
  if (password.length >= MIN_LENGTH) points += 1;
  if (password.length >= 16) points += 1;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) points += 1;
  if (/\d/.test(password) || /[^\w\s]/.test(password)) points += 1;

  // Anything under the minimum is weak whatever else it contains.
  if (password.length < MIN_LENGTH) return 1;
  return Math.max(1, points) as 1 | 2 | 3 | 4;
}

export function NewPassword({ locale, t }: { locale: Locale; t: NewPasswordDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");

  const [busy, setBusy] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [reused, setReused] = useState(false);
  const [noTicket, setNoTicket] = useState(false);
  const [changed, setChanged] = useState(false);

  // The preview toolbar still drives the drawn states; a real response overrides it.
  const offline = state === "offline" || unreachable;
  const rejected = state === "error" || reused;
  const done = state === "done" || changed;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || busy) return;

    setBusy(true);
    setUnreachable(false);
    setReused(false);
    setNoTicket(false);
    try {
      await resetPassword(password);
      // The account name kept for the code step is no longer needed by anything.
      forgetResetUsername();
      setChanged(true);
    } catch (error) {
      if (error instanceof OfflineError) {
        setUnreachable(true);
      } else if (error instanceof ApiError && error.code === "password_reused") {
        setReused(true);
      } else if (error instanceof ApiError && error.code === "unauthenticated") {
        // The reset ticket expired or was already spent. The honest way forward is
        // the start of the flow, not a mysterious refusal on this screen.
        setNoTicket(true);
      } else {
        setReused(true);
      }
    } finally {
      setBusy(false);
    }
  }

  const strength = score(password);
  const longEnough = password.length >= MIN_LENGTH;
  const matches = repeat !== "" && repeat === password;
  const mismatch = repeat !== "" && repeat !== password;
  const ready = longEnough && matches && !offline && !busy;

  const STRENGTH_LABEL = [t.strength_none, t.strength_weak, t.strength_fair, t.strength_good, t.strength_strong];
  const STRENGTH_COLOR = [
    "var(--od-border-6)",
    "var(--od-red-strong)",
    "var(--od-amber)",
    "var(--od-green)",
    "var(--od-green-solid)",
  ];

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
        states={["default", "loading", "error", "done", "offline"]}
      />

      {state === "loading" ? <LoadingCard /> : null}

      {done ? (
        <AuthCard>
          <div className="flex flex-wrap items-center gap-[10px]">
            <span
              className="size-2 flex-none rounded-full"
              style={{ background: "var(--od-green-solid)" }}
            />
            <span
              className="text-[12px] font-bold tracking-[.06em] uppercase"
              style={{ color: "var(--od-green-text)" }}
            >
              {t.done_label}
            </span>
          </div>
          <h1 className="mt-2 mb-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
            {t.done_title}
          </h1>
          <p className="text-od-muted-4 mt-2 text-pretty">{t.done_body}</p>
          <AuthAction href={`/${locale}/login`} disabled={false} className="mt-5">
            {t.done_action}
          </AuthAction>
        </AuthCard>
      ) : null}

      {state !== "loading" && !done ? (
        <AuthCard>
          <form onSubmit={submit}>
          <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">{t.title}</h1>
          <p className="text-od-muted-4 mt-2 text-pretty">{t.body}</p>

          <div className="mt-5 flex flex-col gap-[14px]">
            <div>
              <label htmlFor="password" className="text-od-text-3 block font-medium">
                {t.password}
              </label>
              <input
                id="password"
                type="password"
                autoComplete="new-password"
                dir="ltr"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={offline}
                className={authInputClass(rejected)}
              />

              {/* The meter follows the page direction: it is a fill, not an ordered
                  sequence, so there is nothing outside the interface for it to contradict. */}
              <div className="mt-[10px] flex items-center gap-[10px]">
                <div className="bg-od-border-6 h-[5px] flex-1 overflow-hidden rounded-full">
                  <div
                    className="h-full rounded-full transition-[width]"
                    style={{
                      width: `${(strength / 4) * 100}%`,
                      background: STRENGTH_COLOR[strength],
                    }}
                  />
                </div>
                <span className="text-od-muted-5 min-w-[64px] text-[12.5px]">
                  {STRENGTH_LABEL[strength]}
                </span>
              </div>

              <div className="text-od-muted-5 mt-2 text-[12.5px] text-pretty">
                {longEnough
                  ? t.length_met
                  : interpolate(t.length_rule, { count: MIN_LENGTH })}
              </div>
            </div>

            <div>
              <label htmlFor="repeat" className="text-od-text-3 block font-medium">
                {t.repeat}
              </label>
              <input
                id="repeat"
                type="password"
                autoComplete="new-password"
                dir="ltr"
                value={repeat}
                onChange={(event) => setRepeat(event.target.value)}
                disabled={offline}
                className={authInputClass(mismatch || rejected)}
              />
              {mismatch ? (
                <div className="text-od-red-text-4 mt-2 text-[13px] text-pretty">{t.mismatch}</div>
              ) : null}
            </div>

            {rejected ? (
              <div className="border-od-red-border-3 bg-od-red-bg-4 rounded-[10px] border p-4">
                <div className="text-od-red-text-3 text-[15px] font-semibold text-pretty">
                  {t.reused_title}
                </div>
                <div className="text-od-red-text-6 mt-[5px] text-[13.5px] text-pretty">
                  {t.reused_body}
                </div>
              </div>
            ) : null}

            {noTicket ? (
              <div className="border-od-amber-border bg-od-amber-bg rounded-[10px] border p-[13px]">
                <div className="text-od-amber-text-2 text-[13px] text-pretty">
                  {t.expired_note}{" "}
                  <Link
                    href={`/${locale}/login/forgot`}
                    className="text-od-violet hover:underline"
                  >
                    {t.back_to_forgot}
                  </Link>
                </div>
              </div>
            ) : null}

            <AuthSubmit disabled={!ready || busy}>
              {offline ? t.submit_offline : t.submit}
            </AuthSubmit>
          </div>

          <p className="text-od-muted-5 mt-4 text-[13px] text-pretty">{t.sessions_note}</p>
          </form>
        </AuthCard>
      ) : null}
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
      <div className="h-[22px] w-[64%] rounded-md" style={shimmer("--od-raise-2", "--od-raise-5")} />
      <div
        className="mt-3 h-[15px] w-full rounded-md"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
      <div
        className="mt-[26px] h-[44px] w-full rounded-lg"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
      <div
        className="mt-4 h-[44px] w-full rounded-lg"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
    </AuthCard>
  );
}
