"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  backupDownloadUrl,
  backupOverview,
  checkBackupTarget,
  deleteBackup,
  runBackup,
  stageRestore,
  type BackupOverview,
  type Snapshot,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { BackupDictionary } from "./page";

type Key = keyof BackupDictionary;

/** Bytes as a person reads them. Binary units, because a disk reports binary. */
function bytes(value: number | null): string {
  if (value === null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size < 10 && unit > 0 ? size.toFixed(1) : Math.round(size)} ${units[unit]}`;
}

/**
 * The date the restore dialog asks to be typed.
 *
 * Taken from the timestamp in UTC, because that is what the server compares against.
 * Formatting it in the browser's timezone would ask somebody in Vienna to type
 * yesterday's date for a backup taken at 01:00, and reject them for getting it right.
 */
function confirmDate(snapshot: Snapshot): string {
  return snapshot.started_at.slice(0, 10);
}

function noteFor(snapshot: Snapshot): Key {
  if (snapshot.status === "running") return "snapshot_running";
  if (snapshot.status === "unverified") return "snapshot_failed";
  if (snapshot.kind === "before_update") return "snapshot_before_update";
  if (snapshot.kind === "manual") return "snapshot_manual";
  return "snapshot_nightly";
}

export function Backup({ locale, t }: { locale: Locale; t: BackupDictionary }) {
  const overview = useResource<BackupOverview>(() => backupOverview());
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; bad?: boolean } | null>(null);
  const [restoring, setRestoring] = useState<Snapshot | null>(null);
  const [staged, setStaged] = useState<{ warnings: string[] } | null>(null);

  if (overview.loading && overview.data === null) {
    return (
      <Frame locale={locale}>
        <Notice title={t.loading} />
      </Frame>
    );
  }

  if (overview.error !== null && overview.data === null) {
    return (
      <Frame locale={locale}>
        <Notice
          title={
            overview.error.kind === "offline" ? t.error_offline_title : t.error_failed_title
          }
          body={overview.error.message}
          action={{ label: t.retry, onClick: overview.reload }}
          bad
        />
      </Frame>
    );
  }

  const data = overview.data as BackupOverview;
  const { target } = data;

  const severity = data.state === "none" ? "red" : data.state === "stale" ? "amber" : "ok";
  // From the server, not from this browser's clock. See `last_good_age_days`.
  const daysOld = data.last_good_age_days ?? 0;

  const verdict =
    data.state === "none"
      ? { title: t.verdict_none_title, body: t.verdict_none_body, action: t.verdict_none_action }
      : data.state === "running"
        ? { title: t.running_title, body: t.verdict_running_body_real, action: null }
        : data.state === "stale"
          ? {
              title: t.verdict_stale_title,
              body: interpolate(t.verdict_stale_body_real, { days: String(daysOld) }),
              action: null,
            }
          : {
              // Not the fixture copy, which hard-codes "8 hours ago" and would have
              // said so about a backup taken a minute earlier.
              title: t.verdict_ok_title_real,
              body: interpolate(t.verdict_ok_body_real, {
                when: (data.last_good_at ?? "").slice(0, 16).replace("T", " "),
                daily: String(data.retention.daily),
              }),
              action: null,
            };

  async function act(name: string, work: () => Promise<string | null>) {
    setBusy(name);
    setNotice(null);
    try {
      const message = await work();
      if (message) setNotice({ text: message });
      overview.reload();
    } catch (thrown) {
      setNotice({ text: thrown instanceof Error ? thrown.message : String(thrown), bad: true });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Frame locale={locale}>
      <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-[14px]">
        <div className="min-w-0 max-w-[64ch] flex-[1_1_320px]">
          <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
            {t.title}
          </h1>
          {/* Said plainly, because a self-hoster is the only one who can act on it. */}
          <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
        </div>
        <button
          type="button"
          disabled={busy !== null || !target.writable}
          onClick={() =>
            act("run", async () => {
              const started = await runBackup();
              return started.detail || t.queued;
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 flex-none cursor-pointer rounded-[7px] border p-[9px_15px] text-[13px] font-semibold whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy === "run" ? t.loading : t.run_now}
        </button>
      </div>

      <div
        className="mt-5 flex flex-wrap items-start gap-x-4 gap-y-3 rounded-[11px] border p-[15px_17px]"
        style={{
          borderColor:
            severity === "red"
              ? "var(--od-red-border-3)"
              : severity === "amber"
                ? "var(--od-amber-border-2)"
                : "var(--od-line)",
          background:
            severity === "red"
              ? "var(--od-red-bg-4)"
              : severity === "amber"
                ? "var(--od-amber-bg-2)"
                : "var(--od-panel-deep-2)",
        }}
      >
        <span
          className="mt-[5px] size-[11px] flex-none rounded-full"
          style={{
            background:
              severity === "red"
                ? "#F0605E"
                : severity === "amber"
                  ? "var(--od-amber)"
                  : "var(--od-green)",
            animation: severity === "red" ? "od-ring 1.6s ease-out infinite" : "none",
          }}
        />
        <div className="min-w-[240px] flex-[1_1_320px]">
          <div
            className="text-[16px] font-semibold"
            style={{
              color:
                severity === "red"
                  ? "var(--od-red-text-3)"
                  : severity === "amber"
                    ? "var(--od-amber-text-2)"
                    : "var(--od-text)",
            }}
          >
            {verdict.title}
          </div>
          <div
            className="mt-1 max-w-[70ch] text-[13px] text-pretty"
            style={{
              color:
                severity === "red"
                  ? "var(--od-red-text-6)"
                  : severity === "amber"
                    ? "var(--od-amber-text-3)"
                    : "var(--od-muted)",
            }}
          >
            {verdict.body}
          </div>
          {/* The server's own sentence about the last failure. It names the host or the
              path, which is the part somebody can act on. */}
          {data.last_error ? (
            <div
              dir="ltr"
              className="mono ltr-data mt-[8px] max-w-[80ch] text-[11.5px] [overflow-wrap:anywhere]"
              style={{ color: "var(--od-red-text-6)" }}
            >
              {data.last_error.split("\n").slice(-3).join("\n")}
            </div>
          ) : null}
        </div>
        {verdict.action ? (
          <Link
            href={`/${locale}/settings`}
            className="flex-none cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium whitespace-nowrap"
            style={{
              borderColor: "var(--od-red-border-2)",
              background: "var(--od-red-bg-2)",
              color: "var(--od-red-text-3)",
            }}
          >
            {verdict.action}
          </Link>
        ) : null}
      </div>

      {notice ? (
        <div
          className="mt-4 rounded-[10px] border p-[12px_15px] text-[13px] text-pretty"
          style={{
            borderColor: notice.bad ? "var(--od-red-border-3)" : "var(--od-border-7)",
            background: notice.bad ? "var(--od-red-bg-4)" : "var(--od-panel-deep-2)",
            color: notice.bad ? "var(--od-red-text-6)" : "var(--od-muted)",
          }}
        >
          {notice.text}
        </div>
      ) : null}

      {staged ? (
        <div className="border-od-amber-border-2 bg-od-amber-bg-2 mt-4 rounded-[10px] border p-[15px_17px]">
          <div className="text-[15px] font-semibold text-[color:var(--od-amber-text-2)]">
            {t.restore_staged_title}
          </div>
          <p className="mt-[6px] max-w-[70ch] text-[13px] text-pretty text-[color:var(--od-amber-text-3)]">
            {t.restore_staged_body}
          </p>
          {staged.warnings.length > 0 ? (
            <>
              <div className="mt-3 text-[12px] font-semibold tracking-[.06em] text-[color:var(--od-amber-text-2)] uppercase">
                {t.restore_warning_heading}
              </div>
              <ul className="mt-2 flex list-none flex-col gap-2 p-0">
                {staged.warnings.map((warning) => (
                  <li
                    key={warning}
                    className="max-w-[80ch] text-[12.5px] text-pretty text-[color:var(--od-amber-text-3)]"
                  >
                    {warning}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}

      <section className="mt-[22px]">
        <h2 className="text-od-muted-4 m-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.where_heading}
        </h2>
        <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
          <div className="text-od-muted-5 text-[12.5px]">{t.target_path_label}</div>
          <div
            dir="ltr"
            className="mono ltr-data border-od-stroke-4 text-od-text-2 mt-[7px] rounded-[7px] border border-dashed p-[9px_11px] text-[13px] [overflow-wrap:anywhere]"
          >
            {target.configured ? target.path : t.target_none_path}
          </div>
          <div
            className="mt-[10px] text-[12.5px] text-pretty"
            style={{
              color: target.writable ? "var(--od-green-text)" : "var(--od-amber-text)",
            }}
          >
            {target.writable
              ? interpolate(t.target_writable, { free: bytes(target.free_bytes) })
              : interpolate(t.target_unwritable, { detail: target.detail })}
          </div>
          <div className="mt-[14px] flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() =>
                act("check", async () => {
                  const probe = await checkBackupTarget();
                  return probe.writable
                    ? interpolate(t.target_writable, { free: bytes(probe.free_bytes) })
                    : interpolate(t.target_unwritable, { detail: probe.detail });
                })
              }
              className="border-od-border-2 text-od-muted hover:text-od-text-2 hover:bg-od-raise-4 cursor-pointer rounded-[7px] border bg-transparent p-[8px_14px] text-[12.5px] disabled:opacity-50"
            >
              {busy === "check" ? t.loading : t.check_target}
            </button>
            <Link
              href={`/${locale}/settings`}
              className="border-od-border-2 text-od-muted hover:text-od-text-2 hover:bg-od-raise-4 cursor-pointer rounded-[7px] border bg-transparent p-[8px_14px] text-[12.5px]"
            >
              {t.verdict_none_action}
            </Link>
          </div>
          <div className="border-od-border text-od-muted-5 mt-[14px] border-t pt-3 text-[12.5px] text-pretty">
            {data.include_recordings ? t.recordings_in : t.recordings_out}
          </div>
          <div className="text-od-faint mt-[6px] text-[12.5px] text-pretty">
            {data.include_recordings ? t.schedule_with_audio : t.schedule_without_audio}
          </div>
        </div>
      </section>

      <section className="mt-[22px]">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
          <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.snapshots_heading}
          </h2>
          <span className="text-od-faint text-[12.5px] text-pretty">{t.snapshots_retention}</span>
        </div>
        <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
          {data.snapshots.length === 0 ? (
            <p className="text-od-faint m-0 p-[16px] text-[13px]">{t.no_snapshots}</p>
          ) : (
            data.snapshots.map((snapshot, index) => {
              const good = snapshot.status === "ok";
              const failed = snapshot.status === "failed";
              const tag = failed
                ? t.snapshot_failed_tag
                : snapshot.status === "running"
                  ? t.snapshot_running_tag
                  : snapshot.status === "unverified"
                    ? t.snapshot_unverified_tag
                    : snapshot.kind === "before_update"
                      ? interpolate(t.snapshot_before_update_tag, {
                          version: snapshot.schema_revision ?? "",
                        })
                      : null;

              return (
                <div
                  key={snapshot.id}
                  className="flex flex-wrap items-start gap-x-[14px] gap-y-[10px] p-[13px_16px]"
                  style={{ borderTop: index === 0 ? "none" : "1px solid var(--od-raise-6)" }}
                >
                  <span
                    className="inline-flex size-[21px] flex-none items-center justify-center rounded-full border text-[11.5px] leading-none font-bold"
                    style={{
                      borderColor: good ? "var(--od-green-border)" : "var(--od-amber-border)",
                      background: good ? "rgba(63,185,132,.11)" : "var(--od-amber-bg)",
                      color: good ? "var(--od-green-text)" : "var(--od-amber-text)",
                    }}
                  >
                    {good ? "✓" : "!"}
                  </span>
                  <div className="min-w-[190px] flex-[1_1_220px]">
                    <div className="flex flex-wrap items-center gap-[9px]">
                      <span dir="ltr" className="text-od-text-3 mono ltr-data text-[13px]">
                        {snapshot.started_at.slice(0, 16).replace("T", " ")}
                      </span>
                      {tag ? (
                        <span
                          className="rounded-[5px] border p-[2px_8px] text-[10.5px] font-semibold whitespace-nowrap"
                          style={{
                            borderColor: good ? "var(--od-border-7)" : "var(--od-amber-border)",
                            background: good ? "var(--od-raise-5)" : "var(--od-amber-bg)",
                            color: good ? "var(--od-muted-5)" : "var(--od-amber-text)",
                          }}
                        >
                          {tag}
                        </span>
                      ) : null}
                    </div>
                    <div className="text-od-muted-5 mt-[3px] text-[12.5px] text-pretty">
                      {/* A row whose file has gone says so instead of offering Restore
                          on something that is not there — a swapped USB disk leaves the
                          row behind and takes the archive home. */}
                      {!snapshot.present && snapshot.status !== "failed"
                        ? t.snapshot_missing
                        : t[noteFor(snapshot)]}
                    </div>
                    {snapshot.error ? (
                      <div
                        dir="ltr"
                        className="mono ltr-data mt-[6px] max-w-[80ch] text-[11.5px] [overflow-wrap:anywhere]"
                        style={{ color: "var(--od-amber-text)" }}
                      >
                        {snapshot.error.split("\n").slice(-2).join("\n")}
                      </div>
                    ) : null}
                  </div>
                  <span
                    dir="ltr"
                    className="mono ltr-data text-od-faint flex-none text-[12px] whitespace-nowrap"
                  >
                    {bytes(snapshot.size_bytes)}
                  </span>
                  <div className="flex flex-none flex-wrap gap-[7px]">
                    <button
                      type="button"
                      disabled={!snapshot.present}
                      onClick={() => setRestoring(snapshot)}
                      className="cursor-pointer rounded-md border bg-transparent p-[7px_11px] text-[12.5px] whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-40"
                      style={{
                        borderColor: good ? "var(--od-border-7)" : "var(--od-amber-border)",
                        color: good ? "var(--od-text-3)" : "var(--od-amber-text)",
                      }}
                    >
                      {t.restore}
                    </button>
                    {/* A download is a whole archive, so it is a navigation the browser
                        streams to disk, not a fetch held in memory. */}
                    <a
                      href={snapshot.present ? backupDownloadUrl(snapshot.id) : undefined}
                      aria-disabled={!snapshot.present}
                      className={`border-od-border-7 text-od-muted rounded-md border bg-transparent p-[7px_11px] text-[12.5px] whitespace-nowrap ${
                        snapshot.present
                          ? "hover:text-od-text-2 hover:bg-od-raise-4 cursor-pointer"
                          : "pointer-events-none opacity-40"
                      }`}
                    >
                      {t.download}
                    </a>
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => {
                        if (!window.confirm(t.delete_confirm)) return;
                        void act("delete", async () => {
                          await deleteBackup(snapshot.id);
                          return null;
                        });
                      }}
                      className="border-od-border-7 text-od-muted hover:text-od-text-2 hover:bg-od-raise-4 cursor-pointer rounded-md border bg-transparent p-[7px_11px] text-[12.5px] whitespace-nowrap disabled:opacity-50"
                    >
                      {t.delete}
                    </button>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </section>

      {restoring ? (
        <RestoreDialog
          snapshot={restoring}
          t={t}
          onClose={() => setRestoring(null)}
          onStaged={(warnings) => {
            setRestoring(null);
            setStaged({ warnings });
            overview.reload();
          }}
        />
      ) : null}
    </Frame>
  );
}

function RestoreDialog({
  snapshot,
  t,
  onClose,
  onStaged,
}: {
  snapshot: Snapshot;
  t: BackupDictionary;
  onClose: () => void;
  onStaged: (warnings: string[]) => void;
}) {
  const expected = confirmDate(snapshot);
  const [typed, setTyped] = useState("");
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const [before, after] = t.restore_title.split("{when}");
  // The button is enabled only on an exact match. The server checks this again and is
  // the real gate; doing it here as well is so the person is not told "wrong" after
  // pressing a button labelled "Restore and restart".
  const armed = typed.trim() === expected;

  const facts: Key[] = ["restore_fact_data", "restore_fact_line", "restore_fact_settings"];

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center overflow-auto p-[60px_24px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div className="border-od-red-border-3 bg-od-panel w-full max-w-[560px] rounded-xl border">
        <div className="border-od-line border-b p-[20px_22px]">
          <h2 className="m-0 text-[19px] font-semibold text-[color:var(--od-red-text-3)]">
            {before}
            <span className="mono">{expected}</span>
            {after}
          </h2>
          {/* The only destructive action on this screen, named as such. */}
          <p className="text-od-muted mt-2 max-w-[52ch] text-pretty">{t.restore_body}</p>
        </div>
        <div className="flex flex-col gap-3 p-[18px_22px]">
          {facts.map((fact) => (
            <div key={fact} className="flex items-start gap-[10px]">
              <span className="mt-[7px] size-[6px] flex-none rounded-full bg-[color:var(--od-red-text-4)]" />
              <span className="min-w-0 flex-[1_1_auto] text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
                {t[fact]}
              </span>
            </div>
          ))}
          <div className="border-od-border-6 bg-od-canvas-2 mt-1 rounded-lg border p-[12px_14px]">
            <label className="text-od-muted-5 block text-[12.5px]" htmlFor="restore-confirm">
              {t.restore_confirm_label}
            </label>
            <input
              id="restore-confirm"
              dir="ltr"
              autoComplete="off"
              placeholder={expected}
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              className="border-od-stroke-4 mono text-od-text-2 bg-od-canvas mt-[7px] w-full rounded-[7px] border p-[9px_11px] text-[13px]"
            />
          </div>
          {failure ? (
            <p className="m-0 text-[12.5px] text-pretty text-[color:var(--od-red-text-6)]">
              {failure}
            </p>
          ) : null}
        </div>
        <div className="border-od-line flex flex-wrap justify-end gap-[10px] border-t p-[16px_22px]">
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] whitespace-nowrap"
          >
            {t.cancel}
          </button>
          <button
            type="button"
            disabled={!armed || sending}
            onClick={async () => {
              setSending(true);
              setFailure(null);
              try {
                const result = await stageRestore(snapshot.id, typed.trim());
                onStaged(result.warnings);
              } catch (thrown) {
                setFailure(thrown instanceof Error ? thrown.message : String(thrown));
              } finally {
                setSending(false);
              }
            }}
            className="border-od-red-border-2 bg-od-red-bg-2 cursor-pointer rounded-[7px] border p-[9px_16px] font-semibold whitespace-nowrap text-[color:var(--od-red-text-3)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sending ? t.loading : t.restore_submit}
          </button>
        </div>
      </div>
    </div>
  );
}

function Frame({ locale, children }: { locale: Locale; children: React.ReactNode }) {
  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="settings" liveCalls={0} />
      </div>
      <div className="mx-auto max-w-[1000px] p-[26px_28px_80px]">{children}</div>
    </div>
  );
}

function Notice({
  title,
  body,
  action,
  bad = false,
}: {
  title: string;
  body?: string;
  action?: { label: string; onClick: () => void };
  bad?: boolean;
}) {
  return (
    <div
      className="mt-6 rounded-[11px] border p-[18px_20px]"
      style={{
        borderColor: bad ? "var(--od-red-border-3)" : "var(--od-line)",
        background: bad ? "var(--od-red-bg-4)" : "var(--od-panel-deep-2)",
      }}
    >
      <div
        className="text-[15px] font-semibold"
        style={{ color: bad ? "var(--od-red-text-3)" : "var(--od-text)" }}
      >
        {title}
      </div>
      {body ? (
        <p
          className="mt-[6px] max-w-[70ch] text-[13px] text-pretty"
          style={{ color: bad ? "var(--od-red-text-6)" : "var(--od-muted)" }}
        >
          {body}
        </p>
      ) : null}
      {action ? (
        <button
          type="button"
          onClick={action.onClick}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
        >
          {action.label}
        </button>
      ) : null}
    </div>
  );
}
