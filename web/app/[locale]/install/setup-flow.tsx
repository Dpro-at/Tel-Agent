"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { FirstRun } from "./first-run";
import type { InstallDictionary } from "./page";

import { BrandMark } from "@/components/brands/brand-mark";
import { ApiError, OfflineError, listModels, saveSettings, testModel } from "@/lib/api";
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
  id: string;
  name: string;
  baseUrl: string;
  models: string[];
};

/**
 * The endpoint of each preset is the one its owner documents for OpenAI-format
 * requests; the model names are the current public ones (2026-09), and the field stays
 * free text because they change faster than releases do.
 */
const PRESETS: Preset[] = [
  { id: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1", models: ["gpt-5.6", "gpt-5.5", "chat-latest"] },
  { id: "anthropic", name: "Anthropic", baseUrl: "https://api.anthropic.com/v1", models: ["claude-sonnet-5", "claude-opus-5", "claude-fable-5-1"] },
  { id: "gemini", name: "Gemini", baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai", models: ["gemini-3.5-flash", "gemini-3.1-pro-preview", "gemini-2.5-flash"] },
  { id: "kimi", name: "Kimi", baseUrl: "https://api.moonshot.ai/v1", models: ["kimi-k3", "kimi-k2.7-code"] },
  { id: "deepseek", name: "DeepSeek", baseUrl: "https://api.deepseek.com", models: ["deepseek-v4-flash", "deepseek-v4-pro"] },
  { id: "mistral", name: "Mistral", baseUrl: "https://api.mistral.ai/v1", models: ["mistral-small-latest", "mistral-medium-3-5", "mistral-large-latest"] },
  { id: "groq", name: "Groq", baseUrl: "https://api.groq.com/openai/v1", models: ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.6-27b"] },
  { id: "grok", name: "Grok", baseUrl: "https://api.x.ai/v1", models: ["grok-4.6", "grok-4.5", "grok-4.3"] },
  { id: "qwen", name: "Qwen", baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", models: ["qwen3.8-flash", "qwen3.8-max", "qwen3.7-plus"] },
  { id: "openrouter", name: "OpenRouter", baseUrl: "https://openrouter.ai/api/v1", models: ["openrouter/auto", "google/gemini-3.5-flash", "deepseek/deepseek-v4-pro"] },
  { id: "together", name: "Together AI", baseUrl: "https://api.together.xyz/v1", models: ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "deepseek-ai/DeepSeek-V4-Pro", "moonshotai/Kimi-K2.6"] },
  { id: "perplexity", name: "Perplexity", baseUrl: "https://api.perplexity.ai", models: ["sonar-pro", "sonar"] },
  { id: "custom", name: "", baseUrl: "", models: [] },
];

type Outcome = { text: string; machine?: string; ok: boolean };

/** What the endpoint said when asked which models the typed key may use. */
type Catalogue = {
  status: "idle" | "loading" | "ready" | "unavailable" | "refused";
  models: string[];
};

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
  const [catalogue, setCatalogue] = useState<Catalogue>({ status: "idle", models: [] });
  const asked = useRef(0);

  const chosen = PRESETS.find((p) => p.id === preset) ?? null;
  const ready = baseUrl.trim() !== "" && model.trim() !== "" && key.trim() !== "";

  async function askCatalogue() {
    const address = baseUrl.trim();
    const secret = key.trim();
    if (!address || secret.length < 8) return;
    const ticket = ++asked.current;
    setCatalogue({ status: "loading", models: [] });
    try {
      const { models } = await listModels(address, secret);
      if (ticket !== asked.current) return; // a later question superseded this one
      setCatalogue({ status: models.length ? "ready" : "unavailable", models });
      // Keep a name the operator already chose if the endpoint knows it; otherwise
      // the preset's first suggestion if that is known; otherwise the first listed.
      if (models.length && !models.includes(model.trim())) {
        const suggested = chosen?.models.find((name) => models.includes(name));
        setModel(suggested ?? models[0]);
      }
    } catch (error) {
      if (ticket !== asked.current) return;
      const refused = error instanceof ApiError && error.code === "llm_refused";
      setCatalogue({ status: refused ? "refused" : "unavailable", models: [] });
    }
  }

  // The endpoint is asked once the key looks complete, and again whenever the key or
  // the address changes - after a pause, so a key being pasted in is not asked about
  // character by character.
  const askable = baseUrl.trim() !== "" && key.trim().length >= 8;
  useEffect(() => {
    if (!askable) return;
    const timer = window.setTimeout(() => void askCatalogue(), 700);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberately keyed on the two inputs only
  }, [askable, baseUrl, key]);
  // Below the key length the endpoint is never asked, so whatever was learned for an
  // earlier key is not shown either.
  const status: Catalogue["status"] = askable ? catalogue.status : "idle";

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

      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4" role="radiogroup">
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
            {status === "ready" ? (
              <select
                dir="ltr"
                required
                value={model}
                onChange={(event) => setModel(event.target.value)}
                className={`${inputClass} ltr-data`}
              >
                {catalogue.models.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            ) : (
              <input
                dir="ltr"
                required
                list={chosen.models.length ? "install-model-options" : undefined}
                value={model}
                onChange={(event) => setModel(event.target.value)}
                className={`${inputClass} ltr-data`}
              />
            )}
            {chosen.models.length && status !== "ready" ? (
              <datalist id="install-model-options">
                {chosen.models.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>
            ) : null}
            <span className="text-od-muted-5 mt-1 block text-[12.5px]">
              {status === "loading"
                ? t.cloud_models_loading
                : status === "ready"
                  ? t.cloud_models_ready
                  : status === "refused"
                    ? t.cloud_models_refused
                    : status === "unavailable"
                      ? t.cloud_models_unavailable
                      : t.cloud_model_help}
              {status === "ready" ||
              status === "refused" ||
              status === "unavailable" ? (
                <button
                  type="button"
                  onClick={() => void askCatalogue()}
                  className="text-od-muted-4 hover:text-od-text-2 ms-2 cursor-pointer border-0 bg-transparent p-0 text-[12.5px] underline"
                >
                  {t.cloud_models_refresh}
                </button>
              ) : null}
            </span>
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
  // Every preset's mark is vendored in `components/brands`; only the free-form entry
  // has none, and gets a neutral tile so the row stays even.
  if (preset.id !== "custom") {
    return <BrandMark id={preset.id} size={34} />;
  }
  return (
    <span
      className="border-od-border-6 bg-od-raise-10 text-od-muted-4 inline-flex h-[34px] w-[34px] flex-none items-center justify-center rounded-[10px] border text-[16px] font-semibold"
      aria-hidden="true"
    >
      …
    </span>
  );
}
