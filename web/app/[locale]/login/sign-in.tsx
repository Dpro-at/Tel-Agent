"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, OfflineError, lockedUntil, signIn } from "@/lib/api";

import { DevCredentials } from "@/components/dev-credentials";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import type { Dictionary } from "@/lib/i18n";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import { INSTALLATION } from "./installation";

const UNLOCKS_AT = "11:19";

/**
 * Splits a translated sentence on a placeholder so the value can be rendered as its
 * own element. A hostname is machine data: monospace, and left to right even in Arabic.
 */
function withMachineValue(template: string, token: string, value: React.ReactNode) {
  const [before, after = ""] = template.split(`{${token}}`);
  return (
    <>
      {before}
      <span className="mono ltr-data">{value}</span>
      {after}
    </>
  );
}

export function SignIn({ locale, dictionary }: { locale: Locale; dictionary: Dictionary }) {
  const t = dictionary.auth;
  const [state, setState] = useState<ScreenState>("default");
  /** Set by the card below from a `rate_limited` response; drives the blocked panel. */
  const [serverLock, setServerLock] = useState<Date | null>(null);

  const offline = state === "offline";
  // The panel is shown by the preview toolbar *or* by a real lock from the server.
  // Without the second half, a genuine lockout renders only an inline message and the
  // whole blocked state - the one that says when it lifts - stays unreachable in
  // production while looking finished in the preview.
  const serverLocked = serverLock !== null && serverLock > new Date();
  const blocked = state === "error" || serverLocked;
  const showForm = state === "default" || blocked || offline;

  return (
    <div className="bg-od-canvas text-od-text-2 flex min-h-dvh flex-col">
      <StatePreview state={state} onChange={setState} />

      {offline ? (
        <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-4">
          <span
            className="size-[10px] flex-none rounded-full bg-[#F0605E]"
            style={{ animation: "od-ring 1.6s ease-out infinite" }}
          />
          <div className="min-w-[240px] flex-[1_1_340px]">
            <div className="text-od-red-text text-base font-semibold">{t.offline_title}</div>
            <div className="text-od-red-text-2 mt-[3px]">
              {withMachineValue(
                t.offline_body,
                "host",
                `${INSTALLATION.host}:${INSTALLATION.port}`,
              )}
            </div>
          </div>
          <button
            type="button"
            className="border-od-red-border-2 bg-od-red-bg-2 text-od-red-text-3 hover:bg-od-red-bg-3 cursor-pointer rounded-md border px-[15px] py-[9px] font-medium"
          >
            {t.offline_retry}
          </button>
        </div>
      ) : null}

      <div className="flex flex-1 items-center justify-center px-7 pt-[60px] pb-[90px]">
        {/* max-width, never a fixed width: German runs ~30% longer than English. */}
        <div className="w-full max-w-[420px]">
          <div className="flex flex-wrap items-baseline gap-[10px]">
            <div className="text-od-text text-[20px] font-semibold tracking-[-0.01em]">
              Tel-Agent
            </div>
            <span className="mono ltr-data text-od-faint-2 text-[12px]">{INSTALLATION.version}</span>
          </div>
          {/* The block follows the page direction; only the hostname itself is forced
              LTR, so in Arabic it still starts at the right edge like everything else. */}
          <div className="text-od-muted-5 mt-[6px] text-[12.5px] [overflow-wrap:anywhere]">
            <span className="mono ltr-data">{INSTALLATION.host}</span>
          </div>

          {blocked ? (
            <div className="border-od-red-border-3 bg-od-red-bg-4 mt-[22px] rounded-[10px] border p-4">
              <div className="flex flex-wrap items-center gap-[10px]">
                <span className="size-2 flex-none rounded-full bg-[#F0605E]" />
                <span className="text-od-red-text-5 text-[12px] font-bold tracking-[.06em] uppercase">
                  {t.blocked_label}
                </span>
              </div>
              <div className="text-od-red-text-3 mt-2 text-[15px] font-semibold text-pretty">
                {t.blocked_title}
              </div>
              <div className="text-od-red-text-6 mt-[5px] text-[13.5px] text-pretty">
                {t.blocked_body}
              </div>
              {/* Formatted in the browser: the server sends an instant, and only
                  the browser knows the reader locale and timezone. */}
              <div className="mono ltr-data text-od-red-text-7 mt-[10px] text-[12px]">
                {interpolate(t.blocked_unlocks, {
                  time: serverLock
                    ? serverLock.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
                    : UNLOCKS_AT,
                })}
              </div>
            </div>
          ) : null}

          {state === "empty" ? (
            <div className="border-od-border-6 bg-od-panel-deep-2 mt-[26px] rounded-xl border border-dashed p-[26px]">
              <h1 className="m-0 text-[21px] font-semibold text-pretty">{t.empty_title}</h1>
              <p className="text-od-muted mt-[10px] text-pretty">{t.empty_body}</p>
              <button
                type="button"
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-[18px] w-full cursor-pointer rounded-lg border p-3 text-[15px] font-semibold"
              >
                {t.empty_action}
              </button>
            </div>
          ) : null}

          {showForm ? (
            <SignInCard
              locale={locale}
              dictionary={dictionary}
              blocked={blocked}
              offline={offline}
              onLocked={setServerLock}
            />
          ) : null}

          {state === "loading" ? <LoadingCard /> : null}

          {/* Development only - renders nothing in a production build. */}
          <DevCredentials />

          <div className="text-od-faint-2 mt-5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[12.5px]">
            <span className="text-od-muted-5">{t.self_hosted}</span>
            <Link href={`/${locale}/login`} className="text-od-muted-5 hover:underline">
              {t.continue}
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

function SignInCard({
  locale,
  dictionary,
  blocked,
  offline,
  onLocked,
}: {
  locale: Locale;
  dictionary: Dictionary;
  blocked: boolean;
  offline: boolean;
  onLocked: (when: Date | null) => void;
}) {
  const t = dictionary.auth;
  const router = useRouter();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  /** Set from a real refusal by the server, not from the state-preview toolbar. */
  const [refused, setRefused] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [unlocksAt, setUnlocksAt] = useState<Date | null>(null);

  // The preview toolbar still drives the drawn states, so a designer can look at each
  // one without a server running. A real response overrides it.
  const showRefused = blocked || refused;
  const showOffline = offline || unreachable;
  const locked = unlocksAt !== null && unlocksAt > new Date();
  const disabled = busy || showOffline || locked;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (disabled) return;

    setBusy(true);
    setRefused(false);
    setUnreachable(false);
    try {
      await signIn(username, password);
      // Land where the visitor was going when the middleware turned them away.
      // Absolute URLs are refused: `next` comes from the address bar, and an open
      // redirect on a sign-in page is a phishing primitive.
      // Read at submit time from the address bar rather than through
      // useSearchParams: the hook drags a Suspense boundary into a statically
      // prerendered page for a value only needed on click.
      const next = new URLSearchParams(window.location.search).get("next");
      router.push(
        next && next.startsWith("/") && !next.startsWith("//")
          ? (next as Parameters<typeof router.push>[0])
          : `/${locale}/home`,
      );
    } catch (error) {
      if (error instanceof OfflineError) {
        // A network failure is not a wrong password, and the screen draws them very
        // differently. Telling somebody their password is wrong when their connection
        // is down sends them to reset a password that was fine.
        setUnreachable(true);
      } else if (error instanceof ApiError) {
        // Branching on `code`, never on `message`: the message is English prose from
        // the server, and the strings rendered here are the translated ones.
        setRefused(true);
        const when = error.code === "rate_limited" ? lockedUntil(error) : null;
        setUnlocksAt(when);
        onLocked(when);
      } else {
        setRefused(true);
      }
    } finally {
      setBusy(false);
    }
  }

  const inputClass = [
    "mt-2 w-full rounded-lg border px-[13px] py-[11px] text-[15px] outline-none ltr-data",
    "bg-od-canvas-2 text-od-text-2 focus:border-od-violet",
    showRefused ? "border-od-red-border-2" : "border-od-border-6",
  ].join(" ");

  return (
    <form
      onSubmit={submit}
      className="border-od-line bg-od-panel-deep-3 mt-[26px] rounded-xl border p-[26px]"
    >
      <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">{t.title}</h1>
      <p className="text-od-muted-4 mt-2 text-pretty">{t.subtitle}</p>

      <div className="mt-5 flex flex-col gap-[14px]">
        <div>
          <label htmlFor="username" className="text-od-text-3 block font-medium">
            {t.username}
          </label>
          {/* A username is Latin-script data and stays LTR even in Arabic. */}
          <input
            id="username"
            name="username"
            autoComplete="username"
            dir="ltr"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={busy}
            className={inputClass}
          />
        </div>

        <div>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <label htmlFor="password" className="text-od-text-3 font-medium">
              {t.password}
            </label>
            <Link
              href={`/${locale}/login/forgot`}
              className="text-od-muted-5 text-[12.5px] hover:underline"
            >
              {t.forgot}
            </Link>
          </div>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            dir="ltr"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
            className={inputClass}
          />
          {showRefused ? (
            <div className="text-od-red-text-4 mt-2 text-[13px] text-pretty" role="alert">
              {t.wrong_password}
            </div>
          ) : null}
        </div>

        <button
          type="submit"
          disabled={disabled}
          className={[
            "mt-1 w-full rounded-lg border p-3 text-[15px] font-semibold whitespace-normal",
            disabled
              ? "border-od-border-6 bg-od-raise text-od-faint-2 cursor-not-allowed"
              : "border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer",
          ].join(" ")}
        >
          {showOffline ? t.submit_offline : locked ? t.submit_locked : t.submit}
        </button>
      </div>

      <div className="border-od-border mt-[18px] flex flex-wrap items-center justify-between gap-x-4 gap-y-[10px] border-t pt-4">
        <span className="text-od-muted-5 text-[13px]">{t.key_prompt}</span>
        <Link
          href={`/${locale}/login/key`}
          className="text-od-violet hover:text-od-violet-2 text-[13px] hover:underline"
        >
          {t.key_link}
        </Link>
      </div>
    </form>
  );
}

function LoadingCard() {
  const shimmer = (from: string, to: string) => ({
    background: `linear-gradient(90deg,var(${from}),var(${to}),var(${from}))`,
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  });

  return (
    <div className="border-od-line bg-od-panel-deep-3 mt-[26px] rounded-xl border p-[26px]">
      <div className="h-6 w-2/5 rounded-md" style={shimmer("--od-raise-4", "--od-raise-13")} />
      <div
        className="mt-3 h-[14px] w-[85%] rounded-[5px]"
        style={shimmer("--od-raise-2", "--od-raise-11")}
      />
      <div className="mt-[22px] flex flex-col gap-[14px]">
        {[0, 1, 2].map((index) => (
          <div
            key={index}
            className="border-od-raise-12 h-[46px] w-full rounded-lg border"
            style={shimmer("--od-panel", "--od-raise-7")}
          />
        ))}
      </div>
    </div>
  );
}
