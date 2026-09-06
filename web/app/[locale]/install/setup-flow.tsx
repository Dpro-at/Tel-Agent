"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { FirstRun } from "./first-run";
import type { InstallDictionary } from "./page";

import { BrandMark } from "@/components/brands/brand-mark";
import { ApiError, OfflineError, saveSettings, testModel } from "@/lib/api";
import type { Locale } from "@/lib/locales";

/**
 * The setup that follows the installer, on every platform.
 *
 * The native installers (Inno, `.pkg`, `.deb`/`.rpm`, `docker compose`) unpack, register
 * the services and open the browser here - nothing else. So this page is the one place
 * a new installation is set up, and it looks the same on Windows, macOS, Linux and
 * Docker, in every language the dashboard speaks.
 *
 * Three steps: the account (which already existed as `FirstRun`), how the agent thinks,
 * and connecting the model. The local-model branch is drawn but not wired: the API has
 * one provider kind today, an OpenAI-compatible endpoint, and a model running on the
 * machine itself needs a provider that does not exist yet (`IDEAS.md`). Showing the card
 * anyway tells the operator what is coming, without pretending it works.
 */
export type Step = "account" | "ai" | "cloud";

export function SetupFlow({
  locale,
  t,
  initialStep = "account",
}: {
  locale: Locale;
  t: InstallDictionary;
  initialStep?: Step;
}) {
  const router = useRouter();
  const [step, setStep] = useState<Step>(initialStep);

  function finish() {
    router.replace(`/${locale}/home`);
  }

  return (
    <div className="mx-auto mt-[6vh] max-w-[760px] px-4">
      <Steps current={step} t={t} />
      {step === "account" ? (
        <FirstRun locale={locale} t={t} onCreated={() => setStep("ai")} />
      ) : step === "ai" ? (
        <ChooseAi t={t} onCloud={() => setStep("cloud")} onSkip={finish} />
      ) : (
        <CloudSetup t={t} onBack={() => setStep("ai")} onDone={finish} />
      )}
    </div>
  );
}

// --- Progress ------------------------------------------------------------------

const ORDER: Step[] = ["account", "ai", "cloud"];

function Steps({ current, t }: { current: Step; t: InstallDictionary }) {
  const labels: Record<Step, string> = {
    account: t.step_account,
    ai: t.step_ai,
    cloud: t.step_connect,
  };
  const at = ORDER.indexOf(current);
  return (
    <ol className="m-0 flex list-none flex-wrap items-center gap-x-5 gap-y-2 p-0 text-[13px]">
      {ORDER.map((step, index) => {
        const state = index < at ? "done" : index === at ? "current" : "next";
        return (
          <li key={step} className="flex items-center gap-2">
            <span
              className={[
                "inline-flex h-[22px] w-[22px] items-center justify-center rounded-full border text-[12px] font-semibold",
                state === "done"
                  ? "border-od-violet bg-od-violet text-od-canvas"
                  : state === "current"
                    ? "border-od-violet text-od-text"
                    : "border-od-border-6 text-od-faint-2",
              ].join(" ")}
              aria-hidden="true"
            >
              {state === "done" ? "✓" : index + 1}
            </span>
            <span
              className={
                state === "current" ? "text-od-text font-semibold" : "text-od-muted-5"
              }
            >
              {labels[step]}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// --- Step 2: how the agent thinks ---------------------------------------------------

function ChooseAi({
  t,
  onCloud,
  onSkip,
}: {
  t: InstallDictionary;
  onCloud: () => void;
  onSkip: () => void;
}) {
  return (
    <section className="mt-5">
      <h1 className="m-0 text-[24px] font-semibold tracking-[-0.02em] text-pretty">
        {t.ai_title}
      </h1>
      <p className="text-od-muted-4 mt-2 max-w-[600px] text-pretty">{t.ai_blurb}</p>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <button
          type="button"
          onClick={onCloud}
          aria-label={t.ai_cloud_action}
          className="border-od-line bg-od-panel-deep-3 hover:border-od-violet focus-visible:border-od-violet flex cursor-pointer flex-col items-start gap-3 rounded-xl border p-6 text-start outline-none"
        >
          <span
            className="bg-od-raise-10 text-od-violet inline-flex h-12 w-12 items-center justify-center rounded-lg text-[22px]"
            aria-hidden="true"
          >
            ☁
          </span>
          <span className="text-od-text text-[18px] font-semibold">{t.ai_cloud_title}</span>
          <span className="text-od-muted-4 text-[14px] text-pretty">{t.ai_cloud_body}</span>
          <span className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-auto inline-block rounded-md border px-4 py-2 text-[14px] font-medium">
            {t.ai_cloud_action}
          </span>
        </button>

        <div
          aria-disabled="true"
          className="border-od-line bg-od-panel-deep-3 flex flex-col items-start gap-3 rounded-xl border p-6 opacity-70"
        >
          <span
            className="bg-od-raise-10 text-od-muted-4 inline-flex h-12 w-12 items-center justify-center rounded-lg text-[22px]"
            aria-hidden="true"
          >
            ▣
          </span>
          <span className="text-od-text flex flex-wrap items-center gap-2 text-[18px] font-semibold">
            {t.ai_local_title}
            <span className="border-od-border-6 text-od-muted-5 rounded-md border px-2 py-[2px] text-[11px] font-medium uppercase tracking-[.08em]">
              {t.ai_local_later}
            </span>
          </span>
          <span className="text-od-muted-4 text-[14px] text-pretty">{t.ai_local_body}</span>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={onSkip}
          className="text-od-muted-4 hover:text-od-text-2 cursor-pointer border-0 bg-transparent p-0 text-[13px] underline"
        >
          {t.ai_skip}
        </button>
      </div>
    </section>
  );
}

// --- Step 3: the cloud model --------------------------------------------------------

/**
 * Presets for endpoints that speak the OpenAI wire format. The API knows one provider
 * kind (`openai`); what differs between these is the base URL and the model names, so a
 * preset is nothing but a pre-filled form. `custom` leaves both empty for any other
 * compatible endpoint, including one on the operator's own network.
 */
type Preset = {
  id: "openai" | "mistral" | "gemini" | "custom";
  name: string;
  baseUrl: string;
  models: string[];
  /** Two-letter fallback for endpoints without a mark in `components/brands`. */
  glyph: string;
};

const PRESETS: Preset[] = [
  {
    id: "openai",
    name: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    models: ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"],
    glyph: "OA",
  },
  {
    id: "mistral",
    name: "Mistral",
    baseUrl: "https://api.mistral.ai/v1",
    models: ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest"],
    glyph: "MI",
  },
  {
    id: "gemini",
    name: "Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    models: ["gemini-2.5-flash", "gemini-2.5-pro"],
    glyph: "GE",
  },
  { id: "custom", name: "", baseUrl: "", models: [], glyph: "…" },
];

type Outcome = { text: string; machine?: string; ok: boolean };

function CloudSetup({
  t,
  onBack,
  onDone,
}: {
  t: InstallDictionary;
  onBack: () => void;
  onDone: () => void;
}) {
  const [preset, setPreset] = useState<Preset["id"] | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<"idle" | "saving" | "testing">("idle");
  const [saved, setSaved] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const chosen = PRESETS.find((p) => p.id === preset) ?? null;
  const ready = baseUrl.trim() !== "" && model.trim() !== "" && key.trim() !== "";

  function choose(next: Preset) {
    setPreset(next.id);
    setBaseUrl(next.baseUrl);
    setModel(next.models[0] ?? "");
    setOutcome(null);
    setSaved(false);
  }

  async function saveAndTest(event: React.FormEvent) {
    event.preventDefault();
    if (busy !== "idle" || !ready) return;
    setOutcome(null);
    setBusy("saving");
    try {
      // The key goes into the encrypted column via the settings store - never into
      // `.env` (§B9.2). Provider is always the one kind the API knows.
      await saveSettings({
        "llm.provider": "openai",
        "llm.base_url": baseUrl.trim(),
        "llm.model": model.trim(),
        "llm.api_key": key.trim(),
      });
      setSaved(true);
    } catch (error) {
      setBusy("idle");
      if (error instanceof OfflineError) {
        setOutcome({ text: t.fr_offline, ok: false });
      } else if (error instanceof ApiError && error.code === "encryption_key_missing") {
        setOutcome({ text: t.cloud_no_key, ok: false });
      } else {
        setOutcome({ text: t.cloud_failed, ok: false });
      }
      return;
    }

    setBusy("testing");
    try {
      const result = await testModel();
      setOutcome({ text: t.cloud_reached, machine: result.model, ok: true });
    } catch (error) {
      // Branching on `code`, never on `message`: the message is the server's English.
      if (error instanceof ApiError && error.code === "llm_refused") {
        setOutcome({ text: t.cloud_refused, machine: error.message, ok: false });
      } else if (error instanceof ApiError && error.code === "llm_unreachable") {
        setOutcome({ text: t.cloud_unreachable, ok: false });
      } else if (error instanceof ApiError && error.code === "llm_incomplete") {
        setOutcome({ text: t.cloud_incomplete, machine: error.message, ok: false });
      } else if (error instanceof OfflineError) {
        setOutcome({ text: t.fr_offline, ok: false });
      } else {
        setOutcome({ text: t.cloud_failed, ok: false });
      }
    } finally {
      setBusy("idle");
    }
  }

  const inputClass =
    "border-od-border-6 bg-od-canvas-2 text-od-text-2 focus:border-od-violet mt-2 w-full rounded-lg border px-[13px] py-[11px] text-[15px] outline-none";

  return (
    <section className="mt-5">
      <h1 className="m-0 text-[24px] font-semibold tracking-[-0.02em] text-pretty">
        {t.cloud_title}
      </h1>
      <p className="text-od-muted-4 mt-2 max-w-[600px] text-pretty">{t.cloud_blurb}</p>

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4" role="radiogroup">
        {PRESETS.map((p) => {
          const active = preset === p.id;
          return (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => choose(p)}
              className={[
                "flex min-h-[72px] cursor-pointer items-center gap-3 rounded-lg border px-4 py-3 text-start outline-none",
                "bg-od-panel-deep-3 hover:border-od-violet focus-visible:border-od-violet",
                active ? "border-od-violet" : "border-od-line",
              ].join(" ")}
            >
              <PresetMark preset={p} />
              <span className="text-od-text text-[14px] font-semibold">
                {p.id === "custom" ? t.cloud_preset_custom : p.name}
              </span>
            </button>
          );
        })}
      </div>

      {chosen ? (
        <form onSubmit={saveAndTest} className="mt-6 flex flex-col gap-[14px]">
          <label className="text-od-text-2 block text-[14px]">
            {t.cloud_base_url}
            <input
              dir="ltr"
              type="url"
              required
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              className={`${inputClass} ltr-data`}
            />
            <span className="text-od-muted-5 mt-1 block text-[12.5px]">{t.cloud_base_url_help}</span>
          </label>

          <label className="text-od-text-2 block text-[14px]">
            {t.cloud_model}
            <input
              dir="ltr"
              required
              list={chosen.models.length ? "install-model-options" : undefined}
              value={model}
              onChange={(event) => setModel(event.target.value)}
              className={`${inputClass} ltr-data`}
            />
            {chosen.models.length ? (
              <datalist id="install-model-options">
                {chosen.models.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            ) : null}
            <span className="text-od-muted-5 mt-1 block text-[12.5px]">{t.cloud_model_help}</span>
          </label>

          <label className="text-od-text-2 block text-[14px]">
            {t.cloud_key}
            <input
              dir="ltr"
              type="password"
              required
              autoComplete="off"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              className={`${inputClass} ltr-data`}
            />
            <span className="text-od-muted-5 mt-1 block text-[12.5px]">{t.cloud_key_help}</span>
          </label>

          {outcome ? (
            <p
              className="m-0 rounded-lg border px-4 py-3 text-[13.5px] text-pretty"
              style={{
                borderColor: outcome.ok ? "var(--od-green)" : "var(--od-red-border-2)",
                color: outcome.ok ? "var(--od-text-2)" : "var(--od-red-text-6)",
              }}
            >
              {outcome.text}
              {outcome.machine ? (
                <span dir="ltr" className="mono text-od-muted-5 mt-1 block text-[12px] text-start">
                  {outcome.machine}
                </span>
              ) : null}
            </p>
          ) : null}

          <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
            <button
              type="button"
              onClick={onBack}
              className="text-od-muted-4 hover:text-od-text-2 cursor-pointer border-0 bg-transparent p-0 text-[13px] underline"
            >
              {t.cloud_back}
            </button>
            <div className="flex flex-wrap gap-3">
              <button
                type="submit"
                disabled={busy !== "idle" || !ready}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border px-4 py-[10px] text-[14px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy === "saving"
                  ? t.cloud_saving
                  : busy === "testing"
                    ? t.cloud_testing
                    : t.cloud_save}
              </button>
              {saved ? (
                <button
                  type="button"
                  onClick={onDone}
                  className="border-od-violet bg-od-violet text-od-canvas cursor-pointer rounded-md border px-4 py-[10px] text-[14px] font-semibold"
                >
                  {t.cloud_continue}
                </button>
              ) : null}
            </div>
          </div>
          {saved && outcome && !outcome.ok ? (
            <p className="text-od-muted-5 m-0 text-[12.5px] text-pretty">{t.cloud_saved_note}</p>
          ) : null}
        </form>
      ) : null}
    </section>
  );
}

function PresetMark({ preset }: { preset: Preset }) {
  // `BrandMark` knows the marks the repository carries; the rest get a lettered tile so
  // the row stays even. `BrandMark` returns null for an id it does not have.
  if (preset.id === "openai" || preset.id === "mistral") {
    return <BrandMark id={preset.id} size={34} />;
  }
  return (
    <span
      className="border-od-border-6 bg-od-raise-10 text-od-muted-4 inline-flex h-[34px] w-[34px] flex-none items-center justify-center rounded-[10px] border text-[12px] font-semibold"
      aria-hidden="true"
    >
      {preset.glyph}
    </span>
  );
}
