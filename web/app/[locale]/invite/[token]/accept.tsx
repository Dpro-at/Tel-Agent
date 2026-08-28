"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { acceptInvite, ApiError, readInvite, type InvitePreview } from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { InviteDictionary } from "./page";

/**
 * The public half of D-034: where an invitation becomes an account.
 *
 * The three dead states — invalid, expired, used — are told apart honestly. The
 * person holding a dead link needs to know whether to re-copy it, ask for a new
 * one, or simply sign in; one generic error would leave all three guessing.
 */
type LinkState =
  | { kind: "loading" }
  | { kind: "dead"; reason: "invalid" | "expired" | "used" }
  | { kind: "ready"; preview: InvitePreview };

const ROLE_KEY = {
  admin: "role_admin",
  reception: "role_reception",
  viewer: "role_viewer",
} as const;

export function AcceptInvite({
  locale,
  token,
  t,
}: {
  locale: Locale;
  token: string;
  t: InviteDictionary;
}) {
  const [link, setLink] = useState<LinkState>({ kind: "loading" });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    readInvite(token)
      .then((preview) => {
        if (!alive) return;
        setLink({ kind: "ready", preview });
        setUsername((current) => current || preview.suggested_username);
      })
      .catch((thrown: unknown) => {
        if (!alive) return;
        if (thrown instanceof ApiError && thrown.code === "invite_expired") {
          setLink({ kind: "dead", reason: "expired" });
        } else if (thrown instanceof ApiError && thrown.code === "invite_used") {
          setLink({ kind: "dead", reason: "used" });
        } else {
          setLink({ kind: "dead", reason: "invalid" });
        }
      });
    return () => {
      alive = false;
    };
  }, [token]);

  async function submit() {
    if (password !== repeat) {
      setError(t.err_mismatch);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await acceptInvite(token, username.trim(), password);
      // Accepting signed us in; the dashboard loads fresh under the session.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(`/${locale}/home`);
    } catch (thrown) {
      if (thrown instanceof ApiError) {
        if (thrown.code === "username_taken") setError(t.err_username_taken);
        else if (thrown.code === "invalid_username") setError(t.err_invalid_username);
        else if (thrown.code === "password_too_short") setError(t.err_too_short);
        else if (thrown.code === "invite_expired") setError(t.dead_expired_body);
        else if (thrown.code === "invite_used") setError(t.dead_used_body);
        else setError(thrown.message);
      } else {
        setError(thrown instanceof Error ? thrown.message : String(thrown));
      }
      setPending(false);
    }
  }

  const field =
    "border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[6px] w-full rounded-[7px] border p-[10px_12px] text-[13.5px]";

  return (
    <div className="bg-od-canvas text-od-text-2 flex min-h-dvh items-start justify-center p-[60px_24px] text-[14px] leading-[1.45]">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[460px] rounded-xl border">
        <div className="border-od-line border-b p-[20px_22px]">
          <div className="flex items-baseline gap-2">
            <span className="text-od-text font-semibold tracking-[-0.01em]">Tel-Agent</span>
          </div>
        </div>

        {link.kind === "loading" ? (
          <p className="text-od-muted-5 m-0 p-[22px] text-[13.5px]">{t.loading}</p>
        ) : link.kind === "dead" ? (
          <div className="p-[22px]">
            <h1 className="text-od-text m-0 text-[19px] font-semibold">
              {link.reason === "expired"
                ? t.dead_expired_title
                : link.reason === "used"
                  ? t.dead_used_title
                  : t.dead_invalid_title}
            </h1>
            <p className="text-od-muted-4 mt-[8px] max-w-[46ch] text-pretty">
              {link.reason === "expired"
                ? t.dead_expired_body
                : link.reason === "used"
                  ? t.dead_used_body
                  : t.dead_invalid_body}
            </p>
            {link.reason === "used" ? (
              <Link
                href={`/${locale}/login`}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-[18px] inline-block rounded-md border p-[9px_16px] font-medium hover:no-underline"
              >
                {t.to_login}
              </Link>
            ) : null}
          </div>
        ) : (
          <form
            className="flex flex-col gap-[16px] p-[22px]"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <div>
              <h1 className="text-od-text m-0 text-[19px] font-semibold">
                {interpolate(t.title, { workspace: link.preview.workspace })}
              </h1>
              <p className="text-od-muted-4 mt-[8px] max-w-[48ch] text-pretty">
                {interpolate(t.subtitle, {
                  role: t[ROLE_KEY[link.preview.role as keyof typeof ROLE_KEY] ?? "role_viewer"],
                })}
              </p>
              {link.preview.email ? (
                <div className="mt-[8px] text-[12.5px]">
                  <span className="text-od-faint">{t.email_label}</span>{" "}
                  <span dir="ltr" className="mono ltr-data text-od-muted-5">
                    {link.preview.email}
                  </span>
                </div>
              ) : null}
            </div>

            <label className="block">
              <span className="text-od-muted-5 text-[12.5px]">{t.username_label}</span>
              <input
                dir="ltr"
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className={`mono ltr-data ${field}`}
              />
              <span className="text-od-faint mt-1 block text-[12px] text-pretty">
                {t.username_help}
              </span>
            </label>
            <label className="block">
              <span className="text-od-muted-5 text-[12.5px]">{t.password_label}</span>
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className={field}
              />
              <span className="text-od-faint mt-1 block text-[12px] text-pretty">
                {t.password_hint}
              </span>
            </label>
            <label className="block">
              <span className="text-od-muted-5 text-[12.5px]">{t.repeat_label}</span>
              <input
                type="password"
                autoComplete="new-password"
                value={repeat}
                onChange={(event) => setRepeat(event.target.value)}
                className={field}
              />
            </label>

            {error ? (
              <p className="m-0 text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
                {error}
              </p>
            ) : null}

            <div className="flex items-center justify-end">
              <button
                type="submit"
                disabled={pending || !username.trim() || !password || !repeat}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
              >
                {pending ? t.busy : t.submit}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
