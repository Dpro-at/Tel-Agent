"use client";

import { useEffect, useState } from "react";

import type { InstallDictionary } from "./page";

import {
  ApiError,
  OfflineError,
  localRuntimes,
  pullLocalModel,
  saveSettings,
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

/** Models worth offering to somebody who has nothing yet: small, current, and
 *  reasonable on a machine with 8-16 GB. Sizes are the download, rounded. */
const SUGGESTED = [
  { name: "qwen3:8b", gb: 5.2 },
  { name: "gemma3:4b", gb: 3.3 },
  { name: "llama3.2:3b", gb: 2.0 },
];

/** Where a free runtime comes from. Loopback runtimes are a tool the operator installs,
 *  not a provider Tel-Agent talks to on their behalf. */
const RUNTIME_DOWNLOAD_URL = "https://ollama.com/download";

type Scan =
  | { status: "scanning" }
  | { status: "found"; runtimes: LocalRuntime[]; memoryGb: number | null }
  | { status: "none"; memoryGb: number | null }
  | { status: "failed" };

type Download = { name: string; percent: number | null; done: boolean; failed: boolean };

function gigabytes(bytes: number | null): string {
  if (bytes === null) return "";
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
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
      const { runtimes, memory_gb } = await localRuntimes();
      if (runtimes.length === 0) {
        setScan({ status: "none", memoryGb: memory_gb });
        return;
      }
      setScan({ status: "found", runtimes, memoryGb: memory_gb });
      // The first runtime's first model is the default, so "Use this model" is one
      // click away for the common case of one runtime with one model.
      const first = runtimes.find((runtime) => runtime.models.length > 0);
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
  }, []);

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
          {scan.runtimes.map((runtime) => (
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

              {runtime.models.length > 0 ? (
                <div role="radiogroup" aria-label={t.local_installed}>
                  <div className="text-od-muted-5 grid grid-cols-[1fr_auto] gap-3 px-5 py-2 text-[12px] uppercase tracking-[.08em]">
                    <span>{t.local_installed}</span>
                    <span>{t.local_size}</span>
                  </div>
                  {runtime.models.map((model, index) => {
                    const selected =
                      choice?.runtime.native_url === runtime.native_url &&
                      choice.model === model.id;
                    return (
                      <button
                        key={model.id}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => setChoice({ runtime, model: model.id })}
                        className={[
                          "border-od-line grid w-full cursor-pointer grid-cols-[1fr_auto] items-center gap-3 border-t px-5 py-3 text-start",
                          selected ? "bg-od-raise-10" : "bg-transparent hover:bg-od-raise",
                        ].join(" ")}
                        style={selected ? { boxShadow: "inset 3px 0 var(--od-violet)" } : undefined}
                      >
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="text-od-text mono ltr-data text-[14px] font-semibold">
                            {model.id}
                          </span>
                          {index === 0 ? (
                            <span className="border-od-violet text-od-violet rounded-md border px-2 py-[1px] text-[11px]">
                              {t.local_recommended}
                            </span>
                          ) : null}
                        </span>
                        <span className="text-od-muted-5 text-[13px]">{gigabytes(model.size_bytes)}</span>
                      </button>
                    );
                  })}
                </div>
              ) : null}

              {runtime.can_pull ? (
                <div className="border-od-line border-t px-5 py-4">
                  <strong className="text-od-text text-[15px]">{t.local_downloadable_title}</strong>
                  <p className="text-od-muted-4 mt-1 text-[13px] text-pretty">{t.local_downloadable_body}</p>
                  <div className="mt-3 flex flex-col gap-2">
                    {SUGGESTED.filter(
                      (s) => !runtime.models.some((m) => m.id === s.name || m.id === `${s.name}:latest`),
                    ).map((suggested) => {
                      const active = download?.name === suggested.name;
                      return (
                        <div
                          key={suggested.name}
                          className="grid grid-cols-[1fr_auto] items-center gap-3"
                        >
                          <span className="text-od-text-2 mono ltr-data text-[14px]">
                            {suggested.name}
                            <span className="text-od-muted-5 ms-2 text-[12px]">{suggested.gb} GB</span>
                          </span>
                          {active && !download.done && !download.failed ? (
                            <span className="text-od-muted-4 flex min-w-[160px] flex-col gap-1 text-[12px]">
                              {t.local_downloading}
                              {download.percent !== null ? ` ${download.percent}%` : ""}
                              <span className="bg-od-border-3 block h-[6px] overflow-hidden rounded-full">
                                <span
                                  className="bg-od-violet block h-full rounded-full transition-[width]"
                                  style={{ width: `${download.percent ?? 5}%` }}
                                />
                              </span>
                            </span>
                          ) : active && download.done ? (
                            <span className="text-od-green text-[13px] font-semibold">
                              {t.local_downloaded}
                            </span>
                          ) : (
                            <span className="flex flex-col items-end gap-1">
                              <button
                                type="button"
                                disabled={download !== null && !download.done && !download.failed}
                                onClick={() => void fetchModel(runtime, suggested.name)}
                                className={secondary}
                              >
                                {t.local_download}
                              </button>
                              {active && download.failed ? (
                                <span className="text-[12px]" style={{ color: "var(--od-red-text-6)" }}>
                                  {t.local_download_failed}
                                </span>
                              ) : null}
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>
          ))}
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
