"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { OfflineError, mintChallenge, verifyKeySignature } from "@/lib/api";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import type { Locale } from "@/lib/locales";

import {
  AuthCard,
  AuthFrame,
  AuthSubmit,
  OfflineBanner,
  authInputClass,
  withMachineValue,
} from "../auth-frame";
import { INSTALLATION } from "../installation";

import type { KeyDictionary } from "./page";

/** Machine input, typed verbatim on the caller's own machine. Never translated. */
const SIGN_COMMAND = "ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n tel-agent";

/**
 * Signing in with a key instead of a password.
 *
 * No password is ever typed here and no key ever leaves the caller's machine: the
 * server hands out a challenge, the caller signs it locally, and only the signature
 * comes back. That is the whole reason this screen exists on a self-hosted product -
 * an administrator who never sets a password cannot have one guessed.
 */
export function KeySignIn({ locale, t }: { locale: Locale; t: KeyDictionary }) {
  const router = useRouter();
  const [state, setState] = useState<ScreenState>("default");
  const [username, setUsername] = useState("");
  const [signature, setSignature] = useState("");
  const [copied, setCopied] = useState(false);

  // The real challenge, minted by the server for the username typed above. Fetched
  // when the username field is left rather than on page load: a challenge is minted
  // *for* an account name, and there is none to mint for before one is typed.
  const [challenge, setChallenge] = useState<string | null>(null);
  const [minting, setMinting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const [rejectedByServer, setRejectedByServer] = useState(false);

  // The preview toolbar still drives the drawn states; a real response overrides it.
  const offline = state === "offline" || unreachable;
  const rejected = state === "error" || rejectedByServer;
  const noKeys = state === "none";
  const disabled = offline || noKeys || busy;
  const ready = signature.trim() !== "" && challenge !== null && !disabled;

  async function fetchChallenge() {
    if (!username.trim() || minting) return;
    setMinting(true);
    setUnreachable(false);
    try {
      const minted = await mintChallenge(username.trim());
      setChallenge(minted.challenge);
    } catch (error) {
      if (error instanceof OfflineError) setUnreachable(true);
    } finally {
      setMinting(false);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || challenge === null) return;

    setBusy(true);
    setUnreachable(false);
    setRejectedByServer(false);
    try {
      await verifyKeySignature(username.trim(), challenge, signature);
      router.push(`/${locale}/home`);
    } catch (error) {
      if (error instanceof OfflineError) {
        setUnreachable(true);
      } else {
        // Whatever the reason - bad signature, expired challenge, unregistered key -
        // the screen says one thing. The challenge is spent either way, so a fresh
        // one is fetched for the next attempt.
        setRejectedByServer(true);
        setChallenge(null);
        void fetchChallenge();
      }
    } finally {
      setBusy(false);
    }
  }

  async function copyChallenge() {
    if (challenge === null) return;
    try {
      await navigator.clipboard.writeText(challenge);
      setCopied(true);
      // No timer to clear on unmount: the label resets on the next copy.
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard permission was refused. The string is on screen and selectable,
      // so there is nothing to recover from and nothing worth interrupting for.
    }
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
        states={["default", "loading", "error", "none", "offline"]}
        labels={{ none: "No key registered" }}
      />

      {state === "loading" ? <LoadingCard /> : null}

      {noKeys ? (
        <AuthCard>
          <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
            {t.no_key_title}
          </h1>
          <p className="text-od-muted-4 mt-2 text-pretty">{t.no_key_body}</p>
          <Link
            href={`/${locale}/settings`}
            className="text-od-violet hover:text-od-violet-2 mt-4 inline-block text-[13px] hover:underline"
          >
            {t.no_key_settings}
          </Link>
        </AuthCard>
      ) : null}

      {state !== "loading" && !noKeys ? (
        <AuthCard>
          <form onSubmit={submit}>
            <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
              {t.title}
            </h1>
            <p className="text-od-muted-4 mt-2 text-pretty">{t.body}</p>

            <div className="mt-5">
              <label htmlFor="username" className="text-od-text-3 block font-medium">
                {t.username}
              </label>
              {/* A username is Latin-script data and stays LTR even in Arabic. */}
              <input
                id="username"
                autoComplete="username"
                dir="ltr"
                value={username}
                onChange={(event) => {
                  setUsername(event.target.value);
                  setChallenge(null);
                }}
                onBlur={fetchChallenge}
                disabled={disabled}
                className={authInputClass(rejected)}
              />
            </div>

            <div className="mt-[18px]">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-od-text-3 font-medium">{t.challenge}</span>
                {challenge !== null ? (
                  <button
                    type="button"
                    onClick={copyChallenge}
                    className="text-od-violet hover:text-od-violet-2 cursor-pointer text-[12.5px] hover:underline"
                  >
                    {copied ? t.copied : t.copy}
                  </button>
                ) : null}
              </div>
              {/* Machine output: verbatim, monospace, left to right in every language. */}
              <div
                dir="ltr"
                className="border-od-border-6 bg-od-canvas-2 mono ltr-data text-od-text-3 mt-2 rounded-lg border p-[13px] text-[12.5px] [overflow-wrap:anywhere]"
              >
                {challenge ?? (minting ? "…" : "—")}
              </div>
              <p className="text-od-muted-5 mt-2 text-[12.5px] text-pretty">{t.challenge_note}</p>
            </div>

            <div className="mt-[18px]">
              <span className="text-od-text-3 font-medium">{t.command_label}</span>
              <div
                dir="ltr"
                className="border-od-border-6 bg-od-canvas-2 mono ltr-data text-od-text-3 mt-2 rounded-lg border p-[13px] text-[12.5px] [overflow-wrap:anywhere]"
              >
                {SIGN_COMMAND}
              </div>
            </div>

            <div className="mt-[18px]">
              <label htmlFor="signature" className="text-od-text-3 block font-medium">
                {t.signature}
              </label>
              <textarea
                id="signature"
                rows={4}
                dir="ltr"
                value={signature}
                onChange={(event) => setSignature(event.target.value)}
                disabled={disabled}
                placeholder="-----BEGIN SSH SIGNATURE-----"
                className={`${authInputClass(rejected)} mono resize-y text-[12.5px]`}
              />
              {rejected ? (
                <div className="text-od-red-text-4 mt-2 text-[13px] text-pretty" role="alert">
                  {t.rejected}
                </div>
              ) : null}
            </div>

            <AuthSubmit disabled={!ready} className="mt-4">
              {offline ? t.submit_offline : t.submit}
            </AuthSubmit>

            <div className="border-od-border mt-[18px] flex flex-wrap items-center justify-between gap-x-4 gap-y-[10px] border-t pt-4">
              <span className="text-od-muted-5 text-[13px]">{t.password_prompt}</span>
              <Link
                href={`/${locale}/login`}
                className="text-od-violet hover:text-od-violet-2 text-[13px] hover:underline"
              >
                {t.password_link}
              </Link>
            </div>
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
      <div className="h-[22px] w-[60%] rounded-md" style={shimmer("--od-raise-2", "--od-raise-5")} />
      <div
        className="mt-3 h-[15px] w-full rounded-md"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
      <div
        className="mt-[26px] h-[44px] w-full rounded-lg"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
      <div
        className="mt-4 h-[92px] w-full rounded-lg"
        style={shimmer("--od-raise-2", "--od-raise-5")}
      />
    </AuthCard>
  );
}
