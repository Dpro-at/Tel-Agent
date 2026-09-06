"use client";

import { useEffect, useState } from "react";

import type { InstallDictionary } from "./page";

import {
  ApiError,
  OfflineError,
  localRuntimes,
  pullLocalModel,
  saveSettings,
  startLocalRuntime,
  testModel,
  type LocalRuntime,
} from "@/lib/api";

/**
 * The local branch of the setup: models that run on this computer.
 *
 * The API looks for the runtimes a home machine is likely to have and lists what each
 * one holds. Choosing one saves the runtime's OpenAI-format address like any cloud
 * endpoint, with the word "local" for a key - the runtimes ignore it, and the settings
 * store wants something in the field. Nothing here is a second provider: `agent/` sees
 * an OpenAI-format endpoint on a loopback port.
 *
 * When nothing is installed the screen says what a runtime is and where a free one
 * comes from, then waits to be asked again. When a runtime can download on request, a
 * short list of models that fit an ordinary machine is offered, with the runtime's own
 * progress numbers drawn as a bar.
 */

/**
 * Models a home machine can run, current as of 2026-09, with the download size the
 * runtime reports for each. Merged with whatever the runtime already holds, so the
 * screen shows one list: what is here can be chosen, what is not can be fetched.
 */
const CATALOGUE: { name: string; gb: number }[] = [
  { name: "llama3.2:3b", gb: 2.0 },
  { name: "qwen3:4b", gb: 2.6 },
  { name: "gemma3:4b", gb: 3.3 },
  { name: "phi4-mini", gb: 2.5 },
  { name: "mistral:7b", gb: 4.1 },
  { name: "llama3.1:8b", gb: 4.9 },
  { name: "qwen3:8b", gb: 5.2 },
  { name: "deepseek-r1:8b", gb: 5.2 },
  { name: "gemma3:12b", gb: 8.1 },
];

/** Where a free runtime comes from. Loopback runtimes are a tool the operator installs,
 *  not a provider Tel-Agent talks to on their behalf. */
const RUNTIME_DOWNLOAD_URL = "https://ollama.com/download";

type Scan =
  | { status: "scanning" }
  | { status: "found"; runtimes: LocalRuntime[]; memoryGb: number | null }
  | { status: "none"; memoryGb: number | null }
  | { status: "stopped"; memoryGb: number | null; starting: boolean; failed: boolean }
  | { status: "failed" };

type Download = { name: string; percent: number | null; done: boolean; failed: boolean };

function gigabytes(bytes: number | null): string {
  if (bytes === null) return "";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

/**
 * Installed models, best first. A model routed to the runtime's cloud has no bytes
 * here and is not "on this computer"; below that, the one nearest a 4-5 GB sweet spot
 * that still fits comfortably in memory (half of it) leads, and the rest follow by size.
 */
function rankModels(models: LocalRuntime["models"], memoryGb: number | null) {
  const gb = (bytes: number | null) => (bytes ?? 0) / 1024 ** 3;
  const ceiling = memoryGb === null ? 9 : Math.min(9, memoryGb / 2);
  const score = (size: number) => (size <= 0 || size > ceiling ? Infinity : Math.abs(size - 4.5));
  return [...models].sort((a, b) => {
    const diff = score(gb(a.size_bytes)) - score(gb(b.size_bytes));
    return diff !== 0 ? diff : gb(a.size_bytes) - gb(b.size_bytes);
  });
}

type Row = { name: string; size: string; installed: boolean; recommended: boolean };

/**
 * One list: what the runtime holds (ranked, best first, the leader recommended) and
 * then the catalogue entries it does not hold yet, smallest first. A catalogue name
 * matches an installed one with or without the runtime's ":latest" suffix.
 */
function mergeRows(runtime: LocalRuntime, memoryGb: number | null): Row[] {
  const held = rankModels(runtime.models, memoryGb);
  const has = (name: string) =>
    held.some((m) => m.id === name || m.id === `${name}:latest` || `${m.id}:latest` === name);
  const rows: Row[] = held.map((m, index) => ({
    name: m.id,
    size: gigabytes(m.size_bytes),
    installed: true,
    recommended: index === 0 && (m.size_bytes ?? 0) > 0,
  }));
  for (const entry of [...CATALOGUE].sort((a, b) => a.gb - b.gb)) {
    if (!has(entry.name)) {
      rows.push({ name: entry.name, size: `${entry.gb.toFixed(1)} GB`, installed: false, recommended: false });
    }
  }
  return rows;
}

export function LocalSetup({
  t,
  onBack,
  onDone,
}: {
  t: InstallDictionary;
  onBack: () => void;
  onDone: () => void;
}) {
  const [scan, setScan] = useState<Scan>({ status: "scanning" });
  const [choice, setChoice] = useState<{ runtime: LocalRuntime; model: string } | null>(null);
  const [download, setDownload] = useState<Download | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function look(keepChoice = false) {
    setScan({ status: "scanning" });
    if (!keepChoice) setChoice(null);
    try {
      const { runtimes, memory_gb, installed_but_stopped } = await localRuntimes();
      if (runtimes.length === 0) {
        if (installed_but_stopped) {
          // Installed weeks ago, not started today - the commonest state on a home
          // machine. Start it without being asked; the button stays for a retry.
          setScan({ status: "stopped", memoryGb: memory_gb, starting: true, failed: false });
          void start(memory_gb);
          return;
        }
        setScan({ status: "none", memoryGb: memory_gb });
        return;
      }
      const ranked = runtimes.map((runtime) => ({
        ...runtime,
        models: rankModels(runtime.models, memory_gb),
      }));
      setScan({ status: "found", runtimes: ranked, memoryGb: memory_gb });
      // The best-ranked model of the first runtime is the default, so "Use this
      // model" is one click away for the common case.
      const first = ranked.find((runtime) => runtime.models.length > 0);
      if (!keepChoice && first) setChoice({ runtime: first, model: first.models[0].id });
    } catch {
      setScan({ status: "failed" });
    }
  }

  // The first look happens on arrival. Through a timer rather than directly: the
  // effect must not set state itself, and the initial state already says "scanning".
  useEffect(() => {
    const timer = window.setTimeout(() => void look(), 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on arrival only
  }, []);

  async function start(memoryGb: number | null) {
    setScan({ status: "stopped", memoryGb, starting: true, failed: false });
    try {
      const { answered } = await startLocalRuntime();
      if (answered) {
        await look();
        return;
      }
    } catch {
      // fall through: the button below says so
    }
    setScan({ status: "stopped", memoryGb, starting: false, failed: true });
  }

  async function fetchModel(runtime: LocalRuntime, name: string) {
    setDownload({ name, percent: null, done: false, failed: false });
    try {
      await pullLocalModel(runtime.native_url, name, (event) => {
        if (event.total && event.completed !== undefined) {
          setDownload({
            name,
            percent: Math.min(100, Math.round((event.completed / event.total) * 100)),
            done: false,
            failed: false,
          });
        }
      });
      setDownload({ name, percent: 100, done: true, failed: false });
      // The runtime now holds it; ask again so it appears in the installed list, and
      // make it the choice - downloading a model is choosing it.
      const { runtimes, memory_gb } = await localRuntimes();
      setScan({ status: "found", runtimes, memoryGb: memory_gb });
      const owner = runtimes.find((r) => r.native_url === runtime.native_url) ?? runtime;
      setChoice({ runtime: owner, model: name });
    } catch {
      setDownload({ name, percent: null, done: false, failed: true });
    }
  }

  async function connect() {
    if (!choice || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await saveSettings({
        "llm.provider": "openai",
        "llm.base_url": choice.runtime.base_url,
        "llm.model": choice.model,
        // The runtimes ignore the key; the store wants a value. Never empty, so a
        // half-configured model is still distinguishable from a local one.
        "llm.api_key": "local",
      });
      await testModel();
      onDone();
    } catch (error) {
      if (error instanceof OfflineError) setProblem(t.fr_offline);
      else if (error instanceof ApiError && error.code === "encryption_key_missing")
        setProblem(t.cloud_no_key);
      else setProblem(t.local_failed);
    } finally {
      setBusy(false);
    }
  }

  const link =
    "text-od-muted-4 hover:text-od-text-2 cursor-pointer border-0 bg-transparent p-0 text-[13px] underline";
  const secondary =
    "border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border px-4 py-[10px] text-[14px] font-medium disabled:cursor-not-allowed disabled:opacity-50";
  const primary =
    "border-od-violet bg-od-violet text-od-canvas cursor-pointer rounded-md border px-4 py-[10px] text-[14px] font-semibold disabled:cursor-not-allowed disabled:opacity-50";
  const card = "border-od-line bg-od-panel-deep-3 rounded-xl border";

  return (
    <section className="mt-5">
      <h1 className="m-0 text-[24px] font-semibold tracking-[-0.02em] text-pretty">
        {t.local_title}
      </h1>
      <p className="text-od-muted-4 mt-2 max-w-[600px] text-pretty">{t.local_blurb}</p>

      {scan.status === "scanning" ? (
        <p className="text-od-muted-5 mt-6">{t.local_scanning}</p>
      ) : null}

      {scan.status === "failed" ? (
        <p className="mt-6 text-pretty" style={{ color: "var(--od-red-text-6)" }}>
          {t.fr_offline}
        </p>
      ) : null}

      {scan.status === "stopped" ? (
        <div className={`${card} mt-6 p-6`}>
          <div className="flex items-center gap-3">
            <span className="bg-od-amber inline-block h-3 w-3 rounded-full" aria-hidden="true" />
            <strong className="text-od-text text-[17px]">{t.local_stopped_title}</strong>
          </div>
          <p className="text-od-muted-4 mt-3 max-w-[600px] text-pretty">
            {scan.starting ? t.local_starting : scan.failed ? t.local_start_failed : t.local_stopped_body}
          </p>
          <div className="mt-5 flex flex-wrap gap-3">
            <button
              type="button"
              disabled={scan.starting}
              onClick={() => void start(scan.memoryGb)}
              className={secondary}
            >
              {scan.starting ? t.local_starting : t.local_start}
            </button>
            <button type="button" disabled={scan.starting} onClick={() => void look()} className={secondary}>
              {t.local_rescan}
            </button>
          </div>
        </div>
      ) : null}

      {scan.status === "none" ? (
        <div className={`${card} mt-6 p-6`}>
          <div className="flex items-center gap-3">
            <span className="bg-od-amber inline-block h-3 w-3 rounded-full" aria-hidden="true" />
            <strong className="text-od-text text-[17px]">{t.local_none_title}</strong>
          </div>
          <p className="text-od-muted-4 mt-3 max-w-[600px] text-pretty">{t.local_none_body}</p>
          {scan.memoryGb !== null ? (
            <p className="text-od-muted-5 mt-2 text-[13px]">
              {t.local_memory.replace("{gb}", String(scan.memoryGb))}
            </p>
          ) : null}
          <div className="mt-5 flex flex-wrap gap-3">
            <a
              href={RUNTIME_DOWNLOAD_URL}
              target="_blank"
              rel="noreferrer"
              className={`${secondary} inline-block hover:no-underline`}
            >
              {t.local_get_runtime}
            </a>
            <button type="button" onClick={() => void look()} className={secondary}>
              {t.local_rescan}
            </button>
          </div>
        </div>
      ) : null}

      {scan.status === "found" ? (
        <>
          {scan.runtimes.map((runtime) => {
            const rows = mergeRows(runtime, scan.memoryGb);
            return (
              <div key={runtime.native_url} className={`${card} mt-6 overflow-hidden`}>
                <div className="border-od-line flex flex-wrap items-center gap-3 border-b px-5 py-4">
                  <span className="bg-od-green inline-block h-3 w-3 rounded-full" aria-hidden="true" />
                  <strong className="text-od-text text-[16px]">
                    {runtime.name} {t.local_running}
                  </strong>
                  <span className="text-od-muted-5 text-[13px]">
                    {runtime.models.length} {t.local_models_word}
                  </span>
                  <span className="text-od-faint-2 mono ltr-data ms-auto text-[12px]">
                    {runtime.base_url}
                  </span>
                </div>

                <div className="text-od-muted-5 grid grid-cols-[1fr_auto_auto] gap-4 px-5 py-2 text-[12px] uppercase tracking-[.08em]">
                  <span>{t.local_all_models}</span>
                  <span>{t.local_size}</span>
                  <span className="min-w-[120px] text-end">{t.local_status}</span>
                </div>

                <div role="radiogroup" aria-label={t.local_all_models}>
                  {rows.map((row) => {
                    const selected =
                      row.installed &&
                      choice?.runtime.native_url === runtime.native_url &&
                      choice.model === row.name;
                    const active = download?.name === row.name;
                    const busyRow = active && !download.done && !download.failed;
                    return (
                      <div
                        key={row.name}
                        role={row.installed ? "radio" : undefined}
                        aria-checked={row.installed ? selected : undefined}
                        tabIndex={row.installed ? 0 : undefined}
                        onClick={row.installed ? () => setChoice({ runtime, model: row.name }) : undefined}
                        onKeyDown={
                          row.installed
                            ? (event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                  event.preventDefault();
                                  setChoice({ runtime, model: row.name });
                                }
                              }
                            : undefined
                        }
                        className={[
                          "border-od-line grid grid-cols-[1fr_auto_auto] items-center gap-4 border-t px-5 py-3 text-start",
                          row.installed ? "cursor-pointer" : "",
                          selected ? "bg-od-raise-10" : row.installed ? "hover:bg-od-raise" : "",
                        ].join(" ")}
                        style={selected ? { boxShadow: "inset 3px 0 var(--od-violet)" } : undefined}
                      >
                        <span className="flex flex-wrap items-center gap-2">
                          <span
                            className={[
                              "mono ltr-data text-[14px] font-semibold",
                              row.installed ? "text-od-text" : "text-od-muted-4",
                            ].join(" ")}
                          >
                            {row.name}
                          </span>
                          {row.recommended ? (
                            <span className="border-od-violet text-od-violet rounded-md border px-2 py-[1px] text-[11px]">
                              {t.local_recommended}
                            </span>
                          ) : null}
                        </span>
                        <span className="text-od-muted-5 text-[13px]">{row.size}</span>
                        <span className="flex min-w-[120px] flex-col items-end gap-1">
                          {row.installed ? (
                            <span className="text-od-green text-[13px] font-semibold">✓ {t.local_downloaded}</span>
                          ) : busyRow ? (
                            <span className="text-od-muted-4 flex w-[120px] flex-col gap-1 text-[12px]">
                              {t.local_downloading}
                              {download.percent !== null ? ` ${download.percent}%` : ""}
                              <span className="bg-od-border-3 block h-[6px] overflow-hidden rounded-full">
                                <span
                                  className="bg-od-violet block h-full rounded-full transition-[width]"
                                  style={{ width: `${download.percent ?? 5}%` }}
                                />
                              </span>
                            </span>
                          ) : (
                            <>
                              <button
                                type="button"
                                disabled={!runtime.can_pull || (download !== null && !download.done && !download.failed)}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  void fetchModel(runtime, row.name);
                                }}
                                className={secondary}
                              >
                                {t.local_download}
                              </button>
                              {active && download.failed ? (
                                <span className="text-[12px]" style={{ color: "var(--od-red-text-6)" }}>
                                  {t.local_download_failed}
                                </span>
                              ) : null}
                            </>
                          )}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {scan.memoryGb !== null ? (
            <p className="text-od-muted-5 mt-3 text-[13px]">
              {t.local_memory.replace("{gb}", String(scan.memoryGb))}
            </p>
          ) : null}
        </>
      ) : null}

      {problem ? (
        <p className="mt-4 text-[13px] text-pretty" style={{ color: "var(--od-red-text-6)" }}>
          {problem}
        </p>
      ) : null}
      {busy ? (
        <p className="text-od-muted-5 mt-4 text-[13px] text-pretty">{t.local_loading_hint}</p>
      ) : null}

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-4">
          <button type="button" onClick={onBack} className={link}>
            {t.cloud_back}
          </button>
          {scan.status === "found" ? (
            <button type="button" onClick={() => void look(true)} className={link}>
              {t.local_rescan}
            </button>
          ) : null}
        </div>
        {scan.status === "found" ? (
          <button
            type="button"
            disabled={busy || !choice}
            onClick={() => void connect()}
            title={!choice ? t.local_no_model : undefined}
            className={primary}
          >
            {busy ? t.local_saving : t.local_continue}
          </button>
        ) : null}
      </div>
    </section>
  );
}
