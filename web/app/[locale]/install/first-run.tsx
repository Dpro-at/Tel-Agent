"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import type { InstallDictionary } from "./page";

import { ApiError, OfflineError, completeFirstRun, setupState } from "@/lib/api";
import type { Locale } from "@/lib/locales";

/**
 * First run — the only screen that works before an account exists.
 *
 * `create_first_administrator` and the `/api/setup` entry in the public-path list were
 * both written during the foundations, and nothing served the path between them. So a
 * fresh installation had no way in at all: the seed script is development-only and the
 * alternative was a database client. This is that missing screen.
 *
 * **It is not the drawn install wizard, and does not pretend to be.** That flow chooses
 * a database, ports, speech providers and a phone line — steps whose backends either do
 * not exist yet or cannot exist in a browser, since the application is already running
 * on a database and a port before this page can load. What is real today is an account,
 * a workspace and the web chat channel, so that is what this asks for. The rest returns
 * with the milestones that make it true.
 *
 * The three states are the ones an operator can actually be in: this installation needs
 * setting up, it is already set up, or the server is not answering yet — which on a
 * first run is the likeliest of the three.
 */
export function FirstRun({ locale, t }: { locale: Locale; t: InstallDictionary }) {
  const router = useRouter();
  const [needed, setNeeded] = useState<boolean | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [done, setDone] = useState(false);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setupState()
      .then((state) => live && setNeeded(state.needed))
      // The server not answering is the ordinary case here, not an exception: somebody
      // opening this page for the first time may well have started only the web half.
      .catch(() => live && setUnreachable(true));
    return () => {
      live = false;
    };
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await completeFirstRun({
        username: username.trim(),
        password,
        workspace_name: workspace.trim(),
        email: email.trim() || undefined,
        locale,
      });
      // The response set the session cookie, so this account is signed in already.
      setDone(true);
    } catch (error) {
      if (error instanceof OfflineError) {
        setProblem(t.fr_offline);
      } else if (error instanceof ApiError && error.code === "password_too_short") {
        setProblem(t.fr_short);
      } else if (error instanceof ApiError && error.code === "already_set_up") {
        // Somebody else finished first run in another tab or on another machine.
        setProblem(t.fr_taken);
        setNeeded(false);
      } else {
        // Branching on `code`, never on `message`: the message is English prose from
        // the server and the strings rendered here are the translated ones.
        setProblem(t.fr_failed);
      }
    } finally {
      setBusy(false);
    }
  }

  const shell =
    "border-od-line bg-od-panel-deep-3 mx-auto mt-[8vh] max-w-[560px] rounded-xl border p-[26px]";

  if (unreachable) {
    return (
      <div className={shell}>
        <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
          {t.fr_title}
        </h1>
        <p className="mt-3 text-pretty" style={{ color: "var(--od-red-text-6)" }}>
          {t.fr_offline}
        </p>
      </div>
    );
  }

  if (needed === null) {
    return <p className="text-od-muted-5 mt-[8vh] text-center">{t.fr_checking}</p>;
  }

  if (done) {
    return (
      <div className={shell}>
        <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
          {t.fr_done_title}
        </h1>
        <p className="text-od-muted-4 mt-2 text-pretty">{t.fr_done_body}</p>
        <div className="mt-5 flex flex-col gap-[10px]">
          {/* The two screens that exist and matter before anybody writes in. Named
              rather than rebuilt inside a wizard: both are already wired. */}
          <Link
            href={`/${locale}/settings`}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 rounded-md border p-[11px_16px] font-medium hover:no-underline"
          >
            {t.fr_next_model}
          </Link>
          <Link
            href={`/${locale}/connectors`}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 rounded-md border p-[11px_16px] font-medium hover:no-underline"
          >
            {t.fr_next_widget}
          </Link>
          <button
            type="button"
            onClick={() => router.push(`/${locale}/home`)}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 mt-1 cursor-pointer rounded-md border bg-transparent p-[9px_15px]"
          >
            {t.fr_next_skip}
          </button>
        </div>
      </div>
    );
  }

  if (!needed) {
    return (
      <div className={shell}>
        <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
          {t.fr_already_title}
        </h1>
        <p className="text-od-muted-4 mt-2 text-pretty">{problem ?? t.fr_already_body}</p>
        <Link
          href={`/${locale}/login`}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-5 inline-block rounded-md border p-[10px_16px] font-medium hover:no-underline"
        >
          {t.fr_already_action}
        </Link>
      </div>
    );
  }

  const inputClass =
    "border-od-border-6 bg-od-canvas-2 text-od-text-2 focus:border-od-violet ltr-data mt-2 w-full rounded-lg border px-[13px] py-[11px] text-[15px] outline-none";

  return (
    <form onSubmit={submit} className={shell}>
      <h1 className="m-0 text-[21px] font-semibold tracking-[-0.01em] text-pretty">
        {t.fr_title}
      </h1>
      <p className="text-od-muted-4 mt-2 text-pretty">{t.fr_blurb}</p>

      <div className="mt-5 flex flex-col gap-[14px]">
        <Field id="workspace" label={t.fr_workspace} help={t.fr_workspace_help}>
          {/* A business name is prose and follows the page's direction; the three
              below are Latin-script data and stay left-to-right even in Arabic. */}
          <input
            id="workspace"
            required
            value={workspace}
            onChange={(event) => setWorkspace(event.target.value)}
            className={inputClass.replace(" ltr-data", "")}
          />
        </Field>
        <Field id="username" label={t.fr_username} help={t.fr_username_help}>
          <input
            id="username"
            required
            dir="ltr"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            className={inputClass}
          />
        </Field>
        <Field id="password" label={t.fr_password} help={t.fr_password_help}>
          <input
            id="password"
            required
            dir="ltr"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={inputClass}
          />
        </Field>
        <Field id="email" label={t.fr_email} help={t.fr_email_help}>
          <input
            id="email"
            dir="ltr"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={inputClass}
          />
        </Field>
      </div>

      {problem ? (
        <p className="mt-4 text-[13px] text-pretty" style={{ color: "var(--od-red-text-6)" }}>
          {problem}
        </p>
      ) : null}

      <button
        type="submit"
        disabled={busy}
        className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-5 w-full cursor-pointer rounded-md border p-[11px_16px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? t.fr_submitting : t.fr_submit}
      </button>
    </form>
  );
}

function Field({
  id,
  label,
  help,
  children,
}: {
  id: string;
  label: string;
  help: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="text-od-text-3 block font-medium">
        {label}
      </label>
      {children}
      <p className="text-od-muted-5 mt-[6px] text-[12.5px] text-pretty">{help}</p>
    </div>
  );
}
