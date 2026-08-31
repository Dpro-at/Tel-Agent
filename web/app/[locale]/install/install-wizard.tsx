"use client";

/**
 * The drawn install wizard — **not routed, and kept on purpose.**
 *
 * `/install` renders `first-run.tsx` instead: the part of this flow that has a backend
 * today. What is drawn here and cannot be honest yet is most of it. Choosing a database
 * and ports from a web page is not merely unbuilt, it is impossible in this order — the
 * application is already running on a database and a port before the page can load. The
 * speech steps wait for `agent/providers/{stt,tts}`, which are empty; the machine check
 * has nothing behind it; the line and the closing "call yourself" belong to Milestone 11.
 *
 * It is left here rather than deleted because it is the design this eventually grows
 * back into, and reconstructing it from a git history nobody remembers is worse than
 * one unrouted file that says why it is unrouted. Delete it the day the flow is rebuilt
 * against real endpoints.
 */

import Link from "next/link";
import { useMemo, useState } from "react";

import { BrandMark, brandSlug } from "@/components/brands/brand-mark";
import type { Locale } from "@/lib/locales";

import {
  ChoiceCard,
  FieldRow,
  Heading,
  Note,
  Panel,
  PanelFooter,
  Radio,
  SectionHead,
  StatusMark,
  Tag,
} from "@/components/install/primitives";
import {
  BOTH_HALVES,
  CHANNELS,
  CUSTOM_STEPS,
  LOCAL_MODELS,
  MACHINE,
  MODEL_RAM_GB,
  MODEL_WEIGHT_GB,
  PROVIDERS,
  QUICK_STEPS,
  SEEDS,
  SETUP_VOICES,
  STEP_LABELS,
  STT,
  SYSTEM_LANGUAGES,
  TRADES,
  TTS,
  type Engine,
  type Step,
} from "@/lib/install/data";
import { interpolate } from "@/lib/i18n";

import type { InstallDictionary } from "./page";

type Mode = "quick" | "custom";
type ModelPlacement = "hosted" | "local" | "mixed";

/** Seconds written as "~0.3 s" turned back into milliseconds for the budget sum. */
function toMs(latency: string | undefined): number {
  const digits = (latency ?? "").replace(/[^\d.]/g, "");
  return digits ? parseFloat(digits) * 1000 : 0;
}

export function InstallWizard({ locale, t }: { locale: Locale; t: InstallDictionary }) {
  const [step, setStep] = useState<Step>("language");
  const [mode, setMode] = useState<Mode>("quick");
  const [db, setDb] = useState<"sqlite" | "postgres">("sqlite");
  const [placement, setPlacement] = useState<ModelPlacement>("hosted");
  const [weights, setWeights] = useState("llama-3.1-8b");
  const [provider, setProvider] = useState("anthropic");
  const [hostedModel, setHostedModel] = useState<string | null>(null);
  const [stt, setStt] = useState("local");
  const [tts, setTts] = useState("local");
  const [channels, setChannels] = useState<string[]>(["phone", "web"]);
  const [sysLang, setSysLang] = useState("de-AT");
  const [langQuery, setLangQuery] = useState("");
  const [trade, setTrade] = useState<string | null>(null);
  const [setupVoice, setSetupVoice] = useState("thorsten");

  const steps = useMemo<readonly Step[]>(
    () => (mode === "custom" ? CUSTOM_STEPS : QUICK_STEPS),
    [mode],
  );
  const idx = steps.indexOf(step);

  const hosted = placement === "hosted";
  const sttLocal = stt === "local";
  const ttsLocal = tts === "local";
  const sttOwn = sttLocal || stt === "custom";
  const ttsOwn = ttsLocal || tts === "custom";
  const allLocal = sttLocal && ttsLocal && !hosted;
  const offsiteCount = (sttLocal ? 0 : 1) + (ttsLocal ? 0 : 1) + (hosted ? 1 : 0);

  const chanMb = CHANNELS.filter((c) => channels.includes(c.id)).reduce((sum, c) => sum + c.mb, 0);
  const noChannels = channels.length === 0;

  const roundTrip = Math.round(toMs(STT[stt].latency) + toMs(TTS[tts].latency) + (hosted ? 500 : 900));
  const downloadGb =
    (hosted ? 0 : (MODEL_WEIGHT_GB[weights] ?? 4.9)) +
    (sttLocal ? 1.5 : 0) +
    (ttsLocal ? 0.31 : 0) +
    chanMb / 1000;
  const perCall = (
    (sttOwn ? 0 : stt === "eleven" ? 0.02 : 0.012) +
    (ttsOwn ? 0 : tts === "eleven" ? 0.09 : 0.02) +
    (hosted ? 0.03 : 0)
  ).toFixed(2);

  const activeProvider = PROVIDERS.find((entry) => entry.id === provider) ?? PROVIDERS[0];
  const pickedModel = hostedModel ?? activeProvider.models[0].id ?? t[activeProvider.models[0].idKey!];
  const providerName = activeProvider.name ? t[activeProvider.name] : (activeProvider.nameText ?? "");
  const needRam = MODEL_RAM_GB[weights] ?? 8;
  const enoughRam = hosted || needRam <= MACHINE.ramGb;

  const dbLabel = db === "sqlite" ? "sqlite + vec · /data/telagent.db" : "postgres + pgvector · telagent";

  function go(delta: number) {
    const next = Math.max(0, Math.min(steps.length - 1, idx + delta));
    setStep(steps[next]);
  }

  const showFooter = step !== "done" && step !== "installing";
  const nextLabel =
    step === "admin"
      ? t.install
      : step === "greeting"
        ? t.finish_setup
        : step === "trade" && trade === "empty"
          ? t.continue_empty
          : t.continue;
  const footNote =
    step === "mode"
      ? interpolate(t.steps_count, { count: steps.length })
      : step === "admin"
        ? t.nothing_written
        : "";

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh">
      {/* Review affordance: jump straight to any step. Stripped from a production build. */}
      {process.env.NODE_ENV !== "production" ? (
        <div
          dir="ltr"
          className="border-od-border sticky top-0 z-40 flex max-w-full justify-center overflow-x-auto border-b p-[10px]"
          style={{ background: "linear-gradient(var(--od-canvas), var(--od-scrim))" }}
        >
          <div className="border-od-border-2 bg-od-panel flex flex-none items-center gap-[6px] rounded-full border p-[5px]">
            <span className="text-od-faint ps-[10px] pe-1 text-[11px] tracking-[.09em] uppercase">
              {t.step_chip}
            </span>
            {steps.map((id) => (
              <button
                key={id}
                type="button"
                onClick={() => setStep(id)}
                className={`cursor-pointer rounded-full border px-3 py-[6px] text-[12.5px] whitespace-nowrap ${
                  step === id
                    ? "border-od-stroke bg-od-line-2 text-od-text"
                    : "text-od-muted-4 border-transparent"
                }`}
              >
                {t[STEP_LABELS[id]]}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mx-auto flex max-w-[1140px] flex-wrap items-start gap-[34px] p-[40px_28px_80px]">
        <aside className="min-w-[min(100%,230px)] max-w-[300px] flex-[1_1_250px]">
          <div className="flex items-baseline gap-[9px]">
            <span className="text-od-text text-[19px] font-semibold tracking-[-0.01em]">Tel-Agent</span>
            <span className="mono ltr-data text-od-faint text-[12px]">{MACHINE.version}</span>
          </div>
          <p className="text-od-muted-5 mt-2 max-w-[34ch] text-[13px] text-pretty">{t.side_blurb}</p>

          <div className="mt-[26px] flex flex-col gap-[2px]">
            {steps.map((id, position) => {
              const done = position < idx;
              const now = position === idx;
              return (
                <div
                  key={id}
                  className={`flex items-start gap-[11px] rounded-lg p-[9px_11px] ${now ? "bg-[var(--od-raise-7)]" : ""}`}
                >
                  <span
                    className="mono inline-flex size-[22px] flex-none items-center justify-center rounded-full border text-[11.5px] leading-none font-semibold"
                    style={{
                      borderColor: done
                        ? "var(--od-green-border)"
                        : now
                          ? "var(--od-violet-border)"
                          : "var(--od-border-7)",
                      background: done ? "rgba(63,185,132,.11)" : now ? "var(--od-violet)" : "transparent",
                      color: done ? "var(--od-green-text)" : now ? "#fff" : "var(--od-faint-2)",
                    }}
                  >
                    {done ? "✓" : position + 1}
                  </span>
                  <div className="min-w-0">
                    <div
                      className="text-[13.5px]"
                      style={{
                        fontWeight: now ? 600 : 400,
                        color: now
                          ? "var(--od-text)"
                          : done
                            ? "var(--od-muted-4)"
                            : "var(--od-faint-2)",
                      }}
                    >
                      {t[STEP_LABELS[id]]}
                    </div>
                    {id === "database" ? (
                      <div
                        dir="ltr"
                        className="text-od-faint-2 mono ltr-data mt-[2px] text-start text-[12px]"
                      >
                        {dbLabel}
                      </div>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="border-od-line bg-od-panel-deep-2 text-od-muted-5 mt-6 rounded-[9px] border p-[13px_14px] text-[12.5px] text-pretty">
            {t.side_config_before}
            <span className="mono">{MACHINE.configPath}</span>
            {t.side_config_after}
          </div>
        </aside>

        <main className="min-w-[min(100%,480px)] flex-[2_1_520px]">
          {step === "language" ? (
            <LanguageStep
              t={t}
              value={sysLang}
              onChange={setSysLang}
              query={langQuery}
              onQuery={setLangQuery}
            />
          ) : null}

          {step === "mode" ? <ModeStep t={t} value={mode} onChange={setMode} /> : null}

          {step === "channels" ? (
            <ChannelsStep
              t={t}
              picked={channels}
              onToggle={(id) =>
                setChannels((current) =>
                  current.includes(id) ? current.filter((x) => x !== id) : [...current, id],
                )
              }
              megabytes={chanMb}
              none={noChannels}
            />
          ) : null}

          {step === "model" ? (
            <ModelStep
              t={t}
              placement={placement}
              onPlacement={setPlacement}
              weights={weights}
              onWeights={setWeights}
              provider={provider}
              onProvider={(id) => {
                setProvider(id);
                const entry = PROVIDERS.find((row) => row.id === id) ?? PROVIDERS[0];
                setHostedModel(entry.models[0].id ?? t[entry.models[0].idKey!]);
              }}
              pickedModel={pickedModel}
              onModel={setHostedModel}
            />
          ) : null}

          {step === "voice" ? (
            <VoiceStep
              t={t}
              stt={stt}
              tts={tts}
              onStt={setStt}
              onTts={setTts}
              allLocal={allLocal}
              offsiteCount={offsiteCount}
              roundTrip={roundTrip}
              perCall={perCall}
            />
          ) : null}

          {step === "check" ? (
            <CheckStep
              t={t}
              hosted={hosted}
              enoughRam={enoughRam}
              weights={weights}
              needRam={needRam}
              downloadGb={downloadGb}
            />
          ) : null}

          {step === "database" ? <DatabaseStep t={t} value={db} onChange={setDb} /> : null}
          {step === "host" ? <HostStep t={t} /> : null}
          {step === "admin" ? <AdminStep t={t} /> : null}

          {step === "installing" ? (
            <InstallingStep
              t={t}
              hosted={hosted}
              downloadGb={downloadGb}
              speechLocal={sttLocal || ttsLocal}
              dbLabel={dbLabel}
              modelDetail={hosted ? `${pickedModel} · ${providerName}` : weights}
            />
          ) : null}

          {step === "line" ? <LineStep t={t} /> : null}
          {step === "trade" ? <TradeStep t={t} value={trade} onChange={setTrade} /> : null}
          {step === "greeting" ? (
            <GreetingStep t={t} voice={setupVoice} onVoice={setSetupVoice} ttsLocal={ttsLocal} />
          ) : null}

          {step === "done" ? (
            <DoneStep
              locale={locale}
              t={t}
              custom={mode === "custom"}
              dbLabel={dbLabel}
              modelSummary={
                hosted
                  ? `${pickedModel} · ${providerName}`
                  : placement === "mixed"
                    ? interpolate(t.model_fallback, { model: weights, provider: providerName })
                    : interpolate(t.model_on_machine, { model: weights })
              }
              stt={engineName(t, STT[stt])}
              tts={engineName(t, TTS[tts])}
              roundTrip={roundTrip}
            />
          ) : null}

          {showFooter ? (
            <div className="border-od-border mt-[22px] flex flex-wrap items-center justify-between gap-x-4 gap-y-[10px] border-t pt-[18px]">
              <button
                type="button"
                onClick={() => go(-1)}
                disabled={idx === 0}
                className={`rounded-lg border p-[10px_16px] ${
                  idx === 0
                    ? "text-od-faint-3 pointer-events-none border-transparent"
                    : "border-od-border-2 text-od-muted cursor-pointer"
                }`}
              >
                {t.back}
              </button>
              <div className="flex flex-wrap items-center gap-[10px]">
                <span className="text-od-faint text-[12.5px]">{footNote}</span>
                <button
                  type="button"
                  onClick={() => go(1)}
                  className="border-[color:var(--od-violet-border)] bg-[color:var(--od-violet)] hover:bg-[color:var(--od-violet-2)] inline-flex cursor-pointer items-center gap-[9px] rounded-lg border p-[10px_18px] font-semibold whitespace-nowrap text-white"
                >
                  {nextLabel}
                </button>
              </div>
            </div>
          ) : null}
        </main>
      </div>
    </div>
  );
}

/** An engine is named by its product, or in our own words when it is ours. */
function engineName(t: InstallDictionary, engine: Engine): string {
  return engine.label ? t[engine.label] : (engine.labelText ?? "");
}

function LanguageStep({
  t,
  value,
  onChange,
  query,
  onQuery,
}: {
  t: InstallDictionary;
  value: string;
  onChange: (id: string) => void;
  query: string;
  onQuery: (q: string) => void;
}) {
  const trimmed = query.trim();
  const hits = SYSTEM_LANGUAGES.filter(
    (l) =>
      !trimmed ||
      `${l.native} ${t[l.english]} ${l.id}`.toLowerCase().includes(trimmed.toLowerCase()),
  );
  const picked = SYSTEM_LANGUAGES.find((l) => l.id === value) ?? SYSTEM_LANGUAGES[0];

  return (
    <div>
      <Heading title={t.lang_title} blurb={t.lang_blurb} />

      <div className="border-od-line bg-od-panel-deep-3 mt-5 overflow-hidden rounded-[10px] border">
        <div className="bg-od-canvas-2 flex flex-wrap items-center gap-x-4 gap-y-[10px] border-b border-[color:var(--od-raise-6)] p-[12px_16px]">
          <span className="text-od-faint text-[15px]">⌕</span>
          <input
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder={t.lang_search}
            className="text-od-text-2 min-w-0 flex-[1_1_200px] border-none bg-transparent text-[14px] outline-none"
          />
          <span className="text-od-faint flex-none text-[12px]">
            {hits.length === SYSTEM_LANGUAGES.length
              ? interpolate(t.lang_count, { count: SYSTEM_LANGUAGES.length })
              : interpolate(t.lang_count_of, {
                  shown: hits.length,
                  total: SYSTEM_LANGUAGES.length,
                })}
          </span>
        </div>

        {hits.length === 0 ? (
          <div className="text-od-muted-5 p-[26px_18px] text-[13.5px] text-pretty">
            {interpolate(t.lang_nomatch, { query: trimmed })}
          </div>
        ) : (
          <div
            className="grid gap-px"
            style={{
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              background: "var(--od-raise-6)",
            }}
          >
            {hits.map((language) => {
              const on = value === language.id;
              return (
                <button
                  key={language.id}
                  type="button"
                  onClick={() => onChange(language.id)}
                  className="flex w-full cursor-pointer items-center gap-[10px] border-none p-[13px_16px] text-start"
                  style={{
                    background: on ? "var(--od-canvas-violet)" : "var(--od-panel-deep-3)",
                    boxShadow: on ? "inset 2px 0 0 var(--od-violet)" : "none",
                  }}
                >
                  <span className="min-w-0 flex-[1_1_auto]">
                    <span className="text-od-text block text-[14.5px] font-medium">{language.native}</span>
                    <span className="text-od-faint mt-px block text-[12px]">{t[language.english]}</span>
                  </span>
                  <span className="w-4 flex-none text-[13px] text-[color:var(--od-violet-2)]">
                    {on ? "✓" : ""}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        <div className="bg-od-panel-deep-2 flex flex-wrap items-center justify-between gap-x-[18px] gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[13px_16px]">
          <span className="text-od-muted-5 max-w-[66ch] text-[12.5px] text-pretty">
            {value === "ar" ? t.lang_rtl_note : t.lang_default_note}
          </span>
          <span className="mono ltr-data text-od-faint text-[12px]">{picked.fmt}</span>
        </div>
      </div>
    </div>
  );
}

function ModeStep({
  t,
  value,
  onChange,
}: {
  t: InstallDictionary;
  value: Mode;
  onChange: (mode: Mode) => void;
}) {
  const modes = [
    {
      id: "quick" as const,
      label: t.mode_quick,
      tag: t.tag_recommended,
      tone: "green" as const,
      body: t.mode_quick_body,
      facts: ["SQLite", "localhost:8443", t.fact_self_signed],
    },
    {
      id: "custom" as const,
      label: t.mode_custom,
      tag: t.tag_two_more,
      tone: "neutral" as const,
      body: t.mode_custom_body,
      facts: ["Postgres + pgvector", t.fact_your_hostname, "Let’s Encrypt"],
    },
  ];

  return (
    <div>
      <Heading title={t.mode_title} blurb={t.mode_blurb} />
      <div className="mt-[22px] flex flex-col gap-3">
        {modes.map((mode) => (
          <ChoiceCard key={mode.id} on={value === mode.id} onClick={() => onChange(mode.id)}>
            <span className="flex flex-wrap items-start gap-3">
              <Radio on={value === mode.id} />
              <span className="min-w-0 flex-[1_1_260px]">
                <span className="flex flex-wrap items-center gap-[9px]">
                  <span className="text-od-text text-[16px] font-semibold">{mode.label}</span>
                  <Tag tone={mode.tone}>{mode.tag}</Tag>
                </span>
                <span className="text-od-muted-2 mt-[5px] block text-[13px] text-pretty">{mode.body}</span>
                <span className="mt-[10px] flex flex-wrap gap-x-[14px] gap-y-[6px]">
                  {mode.facts.map((fact) => (
                    <span key={fact} className="mono ltr-data text-od-faint text-[11.5px]">
                      {fact}
                    </span>
                  ))}
                </span>
              </span>
            </span>
          </ChoiceCard>
        ))}
      </div>
    </div>
  );
}

function ChannelsStep({
  t,
  picked,
  onToggle,
  megabytes,
  none,
}: {
  t: InstallDictionary;
  picked: string[];
  onToggle: (id: string) => void;
  megabytes: number;
  none: boolean;
}) {
  return (
    <div>
      <Heading title={t.chan_title} blurb={t.chan_blurb} />

      <div
        className="mt-[22px] grid gap-[10px]"
        style={{ gridTemplateColumns: "repeat(auto-fill, minmax(232px, 1fr))" }}
      >
        {CHANNELS.map((channel) => {
          const on = picked.includes(channel.id);
          return (
            <button
              key={channel.id}
              type="button"
              onClick={() => onToggle(channel.id)}
              className={`flex cursor-pointer flex-col justify-start rounded-[9px] border p-[13px_14px] text-start ${
                on ? "border-od-stroke bg-od-raise-10" : "border-od-line bg-od-panel-deep-3"
              }`}
            >
              <span className="flex items-start gap-[10px]">
                <span
                  className="mt-[2px] inline-flex size-[17px] flex-none items-center justify-center rounded-[5px] border text-[11px] leading-none font-bold text-white"
                  style={{
                    borderColor: on ? "var(--od-violet)" : "var(--od-stroke-5)",
                    background: on ? "var(--od-violet)" : "transparent",
                  }}
                >
                  {on ? "✓" : ""}
                </span>
                {/* A product carries its owner's mark; a channel that is ours carries a
                    drawn glyph. Both sit in the same square, so the column stays even. */}
                {brandSlug(channel.id) ? (
                  <BrandMark id={channel.id} size={24} />
                ) : (
                  <span className="border-od-border-6 bg-od-raise-2 text-od-muted-4 inline-flex size-[24px] flex-none items-center justify-center rounded-[7px] border text-[12px] leading-none">
                    {channel.glyph}
                  </span>
                )}
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-od-text font-semibold">
                      {channel.name ? t[channel.name] : channel.nameText}
                    </span>
                    {channel.core ? <Tag tone="green">{t.tag_most_businesses}</Tag> : null}
                  </span>
                  <span className="text-od-muted-5 mt-[3px] block text-[12px] text-pretty">
                    {t[channel.note]}
                  </span>
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <Note
        tone={none ? "amber" : "plain"}
        title={
          none
            ? t.chan_none_title
            : megabytes >= 1000
              ? interpolate(t.chan_adds_gb, { size: (megabytes / 1000).toFixed(1) })
              : interpolate(t.chan_adds_mb, { size: megabytes })
        }
      >
        {none ? t.chan_none_body : t.chan_note}
      </Note>
    </div>
  );
}

function ModelStep({
  t,
  placement,
  onPlacement,
  weights,
  onWeights,
  provider,
  onProvider,
  pickedModel,
  onModel,
}: {
  t: InstallDictionary;
  placement: ModelPlacement;
  onPlacement: (value: ModelPlacement) => void;
  weights: string;
  onWeights: (id: string) => void;
  provider: string;
  onProvider: (name: string) => void;
  pickedModel: string;
  onModel: (id: string) => void;
}) {
  const choices = [
    {
      id: "hosted" as const,
      label: t.place_hosted,
      tag: t.tag_recommended,
      tone: "violet" as const,
      body: t.place_hosted_body,
    },
    {
      id: "local" as const,
      label: t.place_local,
      tag: t.tag_nothing_leaves,
      tone: "green" as const,
      body: t.place_local_body,
    },
    {
      id: "mixed" as const,
      label: t.place_mixed,
      tag: t.tag_costs_fail,
      tone: "neutral" as const,
      body: t.place_mixed_body,
    },
  ];
  const active = PROVIDERS.find((entry) => entry.id === provider) ?? PROVIDERS[0];

  return (
    <div>
      <Heading title={t.model_title} blurb={t.model_blurb} />

      <div className="mt-[22px] flex flex-col gap-[10px]">
        {choices.map((choice) => (
          <ChoiceCard key={choice.id} on={placement === choice.id} onClick={() => onPlacement(choice.id)}>
            <span className="flex flex-wrap items-start gap-[11px]">
              <Radio on={placement === choice.id} />
              <span className="min-w-0 flex-[1_1_260px]">
                <span className="flex flex-wrap items-center gap-[9px]">
                  <span className="text-od-text font-semibold">{choice.label}</span>
                  <Tag tone={choice.tone}>{choice.tag}</Tag>
                </span>
                <span className="text-od-muted-2 mt-[5px] block text-[12.5px] text-pretty">
                  {choice.body}
                </span>
              </span>
            </span>
          </ChoiceCard>
        ))}
      </div>

      {placement === "local" ? (
        <Panel className="mt-[14px]">
          {LOCAL_MODELS.map((model, position) => {
            const on = weights === model.id;
            return (
              <div
                key={model.id}
                onClick={() => onWeights(model.id)}
                className={`hover:bg-od-raise flex cursor-pointer flex-wrap items-start gap-x-[14px] gap-y-[10px] p-[14px_18px] ${
                  position === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
                }`}
              >
                <Radio on={on} />
                <div className="min-w-[190px] flex-[1_1_220px]">
                  <div className="flex flex-wrap items-center gap-[9px]">
                    <span className="mono ltr-data text-od-text-3 text-[13px]">{model.id}</span>
                    {model.warn ? <Tag tone="amber">{t[model.warn]}</Tag> : null}
                  </div>
                  <div className="text-od-muted-5 mt-[3px] max-w-[56ch] text-[12.5px] text-pretty">
                    {t[model.body]}
                  </div>
                </div>
                <span className="mono ltr-data text-od-faint flex-none text-[12px] whitespace-nowrap">
                  {model.size}
                </span>
              </div>
            );
          })}
        </Panel>
      ) : null}

      {placement === "hosted" ? (
        <div>
          <div
            className="mt-[14px] grid gap-2"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(158px, 1fr))" }}
          >
            {PROVIDERS.map((entry) => {
              const on = provider === entry.id;
              return (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => onProvider(entry.id)}
                  className={`flex cursor-pointer items-center gap-[9px] rounded-[9px] border p-[9px_11px] text-start text-[13px] ${
                    on ? "border-od-stroke bg-od-raise-10 text-od-text" : "border-od-border-7 text-od-muted-4"
                  }`}
                >
                  {brandSlug(entry.id) ? (
                    <BrandMark id={entry.id} size={26} />
                  ) : (
                    <span
                      className="inline-flex size-[26px] flex-none items-center justify-center rounded-[7px] border text-[11.5px] leading-none font-bold"
                      style={{
                        borderColor: `oklch(0.58 0.16 ${entry.hue} / 0.36)`,
                        background: `oklch(0.58 0.16 ${entry.hue} / 0.16)`,
                        color: `oklch(0.55 0.17 ${entry.hue})`,
                      }}
                    >
                      {entry.mark}
                    </span>
                  )}
                  <span className="min-w-0 text-start">
                    {entry.name ? t[entry.name] : entry.nameText}
                  </span>
                </button>
              );
            })}
          </div>

          <Panel className="mt-[14px]">
            <SectionHead label={t.which_models} note={t.which_models_note} />
            {active.models.map((model) => {
              const id = model.id ?? t[model.idKey!];
              const on = pickedModel === id;
              return (
                <div
                  key={id}
                  onClick={() => onModel(id)}
                  className="hover:bg-od-raise flex cursor-pointer flex-wrap items-start gap-x-[14px] gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[13px_18px]"
                >
                  <Radio on={on} />
                  <div className="min-w-[180px] flex-[1_1_220px]">
                    <div className="flex flex-wrap items-center gap-[9px]">
                      {/* A model identifier is machine-readable; a prompt to type one is not. */}
                      <span
                        className={
                          model.id
                            ? "mono ltr-data text-od-text-3 text-[12.5px]"
                            : "text-od-text-3 text-[12.5px]"
                        }
                        dir={model.id ? "ltr" : undefined}
                      >
                        {id}
                      </span>
                      {model.tag ? <Tag tone="green">{t[model.tag]}</Tag> : null}
                    </div>
                    <div className="text-od-muted-5 mt-[3px] max-w-[54ch] text-[12.5px] text-pretty">
                      {t[model.body]}
                    </div>
                  </div>
                  <span className="text-od-faint flex-none text-[12px] whitespace-nowrap">
                    {model.latencyKey ? (
                      t[model.latencyKey]
                    ) : (
                      <span dir="ltr" className="mono ltr-data">
                        {model.latency}
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
          </Panel>

          <Panel className="mt-[14px]">
            <FieldRow label={t.f_endpoint} value={active.endpoint} dim={provider !== "custom"} />
            <FieldRow label={t.f_api_key} value="••••••••••••••••" />
            <PanelFooter note={t[active.cost]} action={t.send_test_prompt} />
          </Panel>

          <Note tone="amber" title={t.leave_title}>
            {t.leave_body}
          </Note>
        </div>
      ) : null}

      {placement === "mixed" ? (
        <Note tone="plain">{t.mixed_note}</Note>
      ) : null}
    </div>
  );
}

function VoiceStep({
  t,
  stt,
  tts,
  onStt,
  onTts,
  allLocal,
  offsiteCount,
  roundTrip,
  perCall,
}: {
  t: InstallDictionary;
  stt: string;
  tts: string;
  onStt: (id: string) => void;
  onTts: (id: string) => void;
  allLocal: boolean;
  offsiteCount: number;
  roundTrip: number;
  perCall: string;
}) {
  const samePair = stt === tts && BOTH_HALVES[stt];
  const pairNote = samePair
    ? interpolate(t.pair_same, { provider: BOTH_HALVES[stt] })
    : BOTH_HALVES[stt] && BOTH_HALVES[tts]
      ? interpolate(t.pair_two, { first: BOTH_HALVES[stt], second: BOTH_HALVES[tts] })
      : null;

  const groups = [
    {
      key: "stt" as const,
      label: t.stt_group,
      note: t.stt_group_note,
      set: STT,
      picked: stt,
      onPick: onStt,
      pairNote: null as string | null,
    },
    {
      key: "tts" as const,
      label: t.tts_group,
      note: t.tts_group_note,
      set: TTS,
      picked: tts,
      onPick: onTts,
      pairNote,
    },
  ];

  return (
    <div>
      <Heading title={t.voice_title} blurb={t.voice_blurb} />

      {groups.map((group) => {
        const endpoint = group.set[group.picked]?.endpoint ?? null;
        return (
          <Panel key={group.key} className="mt-[18px]">
            <SectionHead label={group.label} note={group.note} extra={group.pairNote} />
            {Object.entries(group.set).map(([id, engine]) => {
              const on = group.picked === id;
              const own = engine.tone === "ok";
              return (
                <div
                  key={id}
                  onClick={() => group.onPick(id)}
                  className="hover:bg-od-raise flex cursor-pointer flex-wrap items-start gap-x-[14px] gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[13px_18px]"
                >
                  <Radio on={on} />
                  <div className="min-w-[180px] flex-[1_1_220px]">
                    <div className="flex flex-wrap items-center gap-[9px]">
                      <span className="text-od-text-3 font-medium">{engineName(t, engine)}</span>
                      <Tag tone={own ? "green" : "amber"}>
                        {id === "custom" ? t.tag_your_network : own ? t.tag_on_machine : t.tag_cloud}
                      </Tag>
                    </div>
                    <div className="text-od-muted-5 mt-[3px] max-w-[56ch] text-[12.5px] text-pretty">
                      {t[engine.body]}
                    </div>
                  </div>
                  <div className="flex-none text-end">
                    <div className="text-od-muted-4 text-[12px]">
                      {engine.latencyKey ? (
                        t[engine.latencyKey]
                      ) : (
                        <span dir="ltr" className="mono ltr-data">
                          {engine.latency}
                        </span>
                      )}
                    </div>
                    {engine.size ? (
                      <div className="mono ltr-data text-od-faint-2 mt-[2px] text-[11px]">{engine.size}</div>
                    ) : null}
                  </div>
                </div>
              );
            })}
            {endpoint ? (
              <div className="bg-od-canvas-2 flex flex-wrap items-center justify-between gap-x-[18px] gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[13px_18px]">
                <span className="text-od-muted-5 min-w-[110px] flex-[1_1_130px] text-[12.5px]">
                  {t.f_endpoint}
                </span>
                <span
                  dir="ltr"
                  className="mono ltr-data border-od-border-6 bg-od-panel-deep-3 text-od-text-2 min-w-[min(100%,220px)] flex-[0_1_320px] rounded-[7px] border p-[8px_11px] text-[12px] [overflow-wrap:anywhere]"
                >
                  {endpoint}
                </span>
                <button
                  type="button"
                  className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 flex-none cursor-pointer rounded-[7px] border p-[8px_13px] text-[12.5px] font-medium whitespace-nowrap"
                >
                  {t.test}
                </button>
              </div>
            ) : null}
          </Panel>
        );
      })}

      <Note
        tone={allLocal ? "plain" : "amber"}
        title={
          allLocal
            ? t.all_local_title
            : offsiteCount === 3
              ? t.everything_leaves_title
              : t.some_audio_title
        }
      >
        {allLocal
          ? t.all_local_body
          : interpolate(t.offsite_body, { ms: roundTrip, cost: `€${perCall}` })}
      </Note>
    </div>
  );
}

function CheckStep({
  t,
  hosted,
  enoughRam,
  weights,
  needRam,
  downloadGb,
}: {
  t: InstallDictionary;
  hosted: boolean;
  enoughRam: boolean;
  weights: string;
  needRam: number;
  downloadGb: number;
}) {
  type Check = { tone: "ok" | "warn" | "fail"; name: string; detail: string; value: string };

  const checks: Check[] = [
    {
      tone: "ok",
      name: t.chk_runtime,
      detail: t.chk_runtime_detail,
      value: t.val_ok,
    },
    downloadGb === 0
      ? {
          tone: "ok",
          name: t.chk_disk,
          detail: t.chk_disk_none,
          value: t[MACHINE.diskFree],
        }
      : {
          tone: downloadGb < 60 ? "ok" : "fail",
          name: t.chk_disk,
          detail: interpolate(t.chk_disk_download, { size: downloadGb.toFixed(1) }),
          value: t[MACHINE.diskFree],
        },
    hosted
      ? {
          tone: "ok",
          name: t.chk_memory,
          detail: t.chk_mem_hosted,
          value: interpolate(t.val_gb, { gb: MACHINE.ramGb }),
        }
      : enoughRam
        ? {
            tone: "ok",
            name: t.chk_memory,
            detail: interpolate(t.chk_mem_ok, { model: weights, gb: needRam }),
            value: interpolate(t.val_gb, { gb: MACHINE.ramGb }),
          }
        : {
            tone: "fail",
            name: t.chk_memory,
            detail: interpolate(t.chk_mem_fail, { model: weights, gb: needRam }),
            value: interpolate(t.val_gb, { gb: MACHINE.ramGb }),
          },
    hosted
      ? {
          tone: "ok",
          name: t.chk_graphics,
          detail: t.chk_gfx_hosted,
          value: t.val_cpu_only,
        }
      : {
          tone: "warn",
          name: t.chk_graphics,
          detail: t.chk_gfx_cpu,
          value: t.val_cpu_only,
        },
    {
      tone: "ok",
      name: t.chk_ports,
      detail: t.chk_ports_detail,
      value: "8443 · 5061 · 16384+",
    },
    {
      tone: "fail",
      name: t.chk_smtp,
      detail: t.chk_smtp_detail,
      value: t.val_blocked,
    },
  ];

  return (
    <div>
      <Heading
        title={
          hosted
            ? t.check_title_hosted
            : enoughRam
              ? interpolate(t.check_title_can, { model: weights })
              : interpolate(t.check_title_cannot, { model: weights })
        }
        blurb={
          hosted
            ? t.check_blurb_hosted
            : enoughRam
              ? t.check_blurb_can
              : t.check_blurb_cannot
        }
      />

      <Panel className="mt-5">
        {checks.map((entry) => (
          <div
            key={entry.name}
            className="flex flex-wrap items-start gap-x-[14px] gap-y-[10px] border-b border-[color:var(--od-raise-6)] p-[14px_18px]"
          >
            <StatusMark tone={entry.tone}>
              {entry.tone === "ok" ? "✓" : entry.tone === "warn" ? "!" : "×"}
            </StatusMark>
            <div className="min-w-[200px] flex-[1_1_240px]">
              <div className="text-od-text-3 font-medium">{entry.name}</div>
              <div className="text-od-muted-5 mt-[3px] max-w-[58ch] text-[12.5px] text-pretty">
                {entry.detail}
              </div>
            </div>
            <span className="text-od-muted-4 flex-none text-[12.5px] whitespace-nowrap">
              {entry.value}
            </span>
          </div>
        ))}
      </Panel>

      {!hosted && !enoughRam ? (
        <Note tone="amber" title={t.smaller_title}>
          {t.smaller_body}
        </Note>
      ) : null}

      {hosted ? (
        <Note tone="plain" title={t.hosted_note_title}>
          {t.hosted_note_body}
        </Note>
      ) : null}
    </div>
  );
}

function DatabaseStep({
  t,
  value,
  onChange,
}: {
  t: InstallDictionary;
  value: "sqlite" | "postgres";
  onChange: (value: "sqlite" | "postgres") => void;
}) {
  const choices = [
    { id: "sqlite" as const, label: t.db_file, body: t.db_file_body },
    { id: "postgres" as const, label: t.db_pg, body: t.db_pg_body },
  ];

  return (
    <div>
      <Heading title={t.db_title} blurb={t.db_blurb} />

      <div className="mt-5 flex flex-col gap-[10px]">
        {choices.map((choice) => (
          <ChoiceCard key={choice.id} on={value === choice.id} onClick={() => onChange(choice.id)}>
            <span className="flex flex-wrap items-start gap-[11px]">
              <Radio on={value === choice.id} />
              <span className="min-w-0 flex-[1_1_240px]">
                <span className="text-od-text block font-semibold">{choice.label}</span>
                <span className="text-od-muted-5 mt-1 block text-[12.5px] text-pretty">{choice.body}</span>
              </span>
            </span>
          </ChoiceCard>
        ))}
      </div>

      {value === "postgres" ? (
        <Panel className="mt-[14px]">
          <FieldRow label={t.f_host} value="db.wagner-partner.local" />
          <FieldRow label={t.f_port} value="5432" />
          <FieldRow label={t.f_dbname} value="telagent" />
          <FieldRow label={t.f_user} value="telagent" />
          <FieldRow label={t.f_password} value="••••••••••••" />
          <PanelFooter note={t.db_footer_note} action={t.db_test} />
        </Panel>
      ) : null}
    </div>
  );
}

function HostStep({ t }: { t: InstallDictionary }) {
  return (
    <div>
      <Heading title={t.host_title} blurb={t.host_blurb} />
      <Panel className="mt-5">
        <FieldRow label={t.f_hostname} help={t.f_hostname_help} value={MACHINE.hostname} />
        <FieldRow label={t.f_webport} value="8443" />
        <FieldRow label={t.f_tls} help={t.f_tls_help} value="Let’s Encrypt" />
        <FieldRow label={t.f_sip} help={t.f_sip_help} value="5061 · TLS" />
        <FieldRow label={t.f_rtp} help={t.f_rtp_help} value="16384–16584" />
        <FieldRow label={t.f_proxy} help={t.f_proxy_help} value={t.v_yes} last />
      </Panel>
      <Note tone="plain">{t.host_note}</Note>
    </div>
  );
}

function AdminStep({ t }: { t: InstallDictionary }) {
  return (
    <div>
      <Heading title={t.admin_title} blurb={t.admin_blurb} />
      <Panel className="mt-5">
        <FieldRow label={t.f_name} value="Mohamed" />
        <FieldRow label={t.f_email} value={MACHINE.adminEmail} />
        <FieldRow label={t.f_password} value="••••••••••••••" />
        <FieldRow label={t.f_pass_repeat} value="••••••••••••••" />
        <div className="text-od-faint p-[14px_18px] text-[12.5px] text-pretty">{t.admin_note}</div>
      </Panel>
    </div>
  );
}

function InstallingStep({
  t,
  hosted,
  downloadGb,
  speechLocal,
  dbLabel,
  modelDetail,
}: {
  t: InstallDictionary;
  hosted: boolean;
  downloadGb: number;
  speechLocal: boolean;
  dbLabel: string;
  modelDetail: string;
}) {
  const blurb =
    downloadGb === 0
      ? t.inst_blurb_none
      : interpolate(t.inst_blurb, {
          what: hosted ? t.inst_dl_speech : t.inst_dl_ai,
          size: downloadGb.toFixed(1),
        });

  /** A path or a model id is machine output; "valid 90 days" is not. */
  const tasks: {
    state: "done" | "run" | "wait";
    name: string;
    detail?: string;
    detailCopy?: string;
    percent?: number;
  }[] = [
    { state: "done", name: t.task_config, detail: MACHINE.configPath },
    { state: "done", name: t.task_db, detail: dbLabel },
    { state: "done", name: t.task_tls, detailCopy: t.task_tls_detail },
    speechLocal
      ? {
          state: "run",
          name: t.inst_dl_speech,
          detailCopy: t.task_speech_detail,
          percent: 66,
        }
      : { state: "run", name: t.task_verify_speech },
    {
      state: "wait",
      name: hosted ? t.task_verify_cloud : t.task_dl_model,
      detail: modelDetail,
    },
    { state: "wait", name: t.task_start },
  ];

  return (
    <div>
      <Heading title={t.inst_title} blurb={blurb} />
      <Panel className="mt-5">
        {tasks.map((task) => (
          <div
            key={task.name}
            className="flex flex-wrap items-start gap-x-[14px] gap-y-[10px] border-b border-[color:var(--od-raise-6)] p-[14px_18px]"
          >
            <StatusMark
              tone={task.state === "done" ? "ok" : task.state === "run" ? "violet" : "idle"}
              spin={task.state === "run"}
            >
              {task.state === "done" ? "✓" : task.state === "run" ? "◐" : ""}
            </StatusMark>
            <div className="min-w-[200px] flex-[1_1_240px]">
              <div
                className="font-medium"
                style={{ color: task.state === "wait" ? "var(--od-faint-2)" : "var(--od-text-3)" }}
              >
                {task.name}
              </div>
              {task.detail ? (
                <div dir="ltr" className="mono ltr-data text-od-faint mt-[3px] text-start text-[12px]">
                  {task.detail}
                </div>
              ) : null}
              {task.detailCopy ? (
                <div className="text-od-faint mt-[3px] text-[12px]">{task.detailCopy}</div>
              ) : null}
              {task.percent != null ? (
                <div className="mt-2 h-1 rounded-full bg-[var(--od-raise-4)]">
                  <span
                    className="block h-1 rounded-full bg-[color:var(--od-violet)]"
                    style={{ width: `${task.percent}%` }}
                  />
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </Panel>
      <div className="text-od-faint mt-[14px] text-[12.5px] text-pretty">
        {t.inst_close_before}
        <span className="mono">{MACHINE.logPath}</span>
        {t.inst_close_after}
      </div>
    </div>
  );
}

function LineStep({ t }: { t: InstallDictionary }) {
  return (
    <div>
      <Heading title={t.line_title} blurb={t.line_blurb} />
      <Panel className="mt-5">
        <FieldRow
          label={t.f_provider_host}
          help={t.f_provider_host_help}
          value="sip.easybell.de"
        />
        <FieldRow label={t.f_username} help={t.f_username_help} value="4319876543" />
        <FieldRow label={t.f_password} help={t.f_password_help} value="••••••••••••" />
        <FieldRow label={t.f_business} help={t.f_business_help} value={MACHINE.business} />
        <PanelFooter note={t.line_footer_note} action={t.line_test} />
      </Panel>
    </div>
  );
}

function TradeStep({
  t,
  value,
  onChange,
}: {
  t: InstallDictionary;
  value: string | null;
  onChange: (id: string) => void;
}) {
  const seed = value ? SEEDS[value] : null;

  return (
    <div>
      <Heading title={t.trade_title} blurb={t.trade_blurb} />

      <div className="mt-5 flex flex-wrap gap-[10px]">
        {TRADES.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => onChange(entry.id)}
            className="min-w-[min(100%,200px)] flex-[1_1_210px] cursor-pointer rounded-[10px] border p-[13px_15px] text-start"
            style={{
              borderColor: value === entry.id ? "var(--od-violet-border)" : "var(--od-border-4)",
              background: value === entry.id ? "var(--od-canvas-violet)" : "var(--od-panel-deep-3)",
            }}
          >
            <span className="text-od-text block font-semibold text-pretty">{t[entry.label]}</span>
            <span className="text-od-muted-5 mt-1 block text-[12.5px] text-pretty">{t[entry.note]}</span>
          </button>
        ))}
      </div>

      {seed ? (
        <div className="border-od-line bg-od-panel-deep-3 mt-5 overflow-hidden rounded-[11px] border">
          <div className="border-b border-[color:var(--od-raise-6)] p-[16px_18px_12px]">
            <div className="text-od-muted-4 text-[13px] font-semibold tracking-[.07em] uppercase">
              {t.what_writes}
            </div>
            <div className="text-od-faint mt-[5px] text-[12.5px]">
              {interpolate(t.seed_counts, {
                services: seed.services.length,
                fields: seed.fields.length,
                rules: seed.rules.length,
                qa: seed.qa,
              })}
            </div>
          </div>

          {value === "empty" ? (
            <div className="text-od-muted-5 p-[18px] text-[13.5px] text-pretty">
              {t.trade_empty_body}
            </div>
          ) : (
            <div>
              <div
                className="grid gap-px"
                style={{
                  gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                  background: "var(--od-raise-6)",
                }}
              >
                {[
                  { id: "services", label: t.col_services, items: seed.services },
                  { id: "fields", label: t.col_fields, items: seed.fields },
                  { id: "rules", label: t.col_rules, items: seed.rules },
                ].map((column) => (
                  <div key={column.id} className="bg-od-panel-deep-3 p-[16px_18px]">
                    <div className="text-od-faint text-[12px] tracking-[.06em] uppercase">
                      {column.label}
                    </div>
                    <div className="mt-[10px] flex flex-col gap-[6px]">
                      {column.items.map((item) => (
                        <span key={item} className="text-od-text-3 text-[13.5px] text-pretty">
                          {t[item]}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              <div className="border-t border-[color:var(--od-raise-6)] p-[16px_18px]">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
                  <div className="text-od-faint text-[12px] tracking-[.06em] uppercase">
                    {t.starting_instructions}
                  </div>
                  <span className="text-od-faint text-[12.5px]">{t.editable_on}</span>
                </div>
                <div className="border-od-border-6 bg-od-canvas-2 mt-[10px] rounded-[9px] border p-[14px_16px] text-[13.5px] leading-[1.75] text-pretty text-[color:var(--od-text-4)]">
                  {seed.prompt ? t[seed.prompt] : null}
                </div>
              </div>
            </div>
          )}

          <div className="bg-od-panel-deep-2 text-od-faint border-t border-[color:var(--od-raise-6)] p-[13px_18px] text-[12.5px] text-pretty">
            {t.trade_footer}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function GreetingStep({
  t,
  voice,
  onVoice,
  ttsLocal,
}: {
  t: InstallDictionary;
  voice: string;
  onVoice: (id: string) => void;
  ttsLocal: boolean;
}) {
  return (
    <div>
      <Heading title={t.greet_title} blurb={t.greet_blurb} />

      <div className="mt-5">
        <label className="text-od-text-3 font-medium">{t.greet_label}</label>
        <div className="border-od-border-6 bg-od-canvas-2 mt-2 rounded-lg border p-[14px_16px] text-[15px] leading-[1.7] text-pretty text-[color:var(--od-text-4)]">
          {interpolate(t.greet_text, { business: MACHINE.business })}
        </div>
        <div className="text-od-faint mt-[6px] text-[12.5px]">{t.greet_note}</div>
      </div>

      <div className="mt-5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
          <label className="text-od-text-3 font-medium">{t.voice_label}</label>
          <span className="text-od-faint text-[12.5px] text-pretty">
            {ttsLocal ? t.voice_local_note : t.voice_engine_note}
          </span>
        </div>
        <div className="mt-[10px] flex flex-col gap-[10px]">
          {SETUP_VOICES.map((option) => {
            const on = voice === option.id;
            return (
              <button
                key={option.id}
                type="button"
                onClick={() => onVoice(option.id)}
                className="flex cursor-pointer flex-wrap items-center gap-x-[14px] gap-y-[10px] rounded-[10px] border p-[13px_16px] text-start"
                style={{
                  borderColor: on ? "var(--od-violet-border)" : "var(--od-border-4)",
                  background: on ? "var(--od-canvas-violet)" : "var(--od-panel-deep-3)",
                }}
              >
                <span className="min-w-[200px] flex-[1_1_220px] text-start">
                  <span className="text-od-text-2 block font-semibold">{option.name}</span>
                  <span className="text-od-muted-5 mt-[3px] block text-[12.5px] text-pretty">
                    {t[option.desc]}
                  </span>
                </span>
                <span
                  className="flex-none rounded-md border p-[5px_11px] text-[12.5px] font-semibold"
                  style={{
                    borderColor: on ? "var(--od-violet-border)" : "var(--od-border-2)",
                    background: on ? "rgba(139,124,255,.14)" : "transparent",
                    color: on ? "var(--od-violet-3)" : "var(--od-muted-4)",
                  }}
                >
                  {on ? t.selected : t.use_voice}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-od-line bg-od-panel-deep-2 mt-5 flex flex-wrap items-center justify-between gap-x-5 gap-y-3 rounded-[10px] border p-[14px_16px]">
        <div className="max-w-[60ch] min-w-0">
          <div className="text-od-text-3 font-medium">{t.book_title}</div>
          <div className="text-od-muted-5 mt-[3px] text-[13px] text-pretty">{t.book_body}</div>
        </div>
        <button
          type="button"
          className="border-[color:var(--od-border-9)] bg-[var(--od-raise-5)] text-[color:var(--od-text-5)] hover:bg-[var(--od-line-2)] hover:text-[color:var(--od-text)] cursor-pointer rounded-md border p-[8px_14px] text-[13px]"
        >
          {t.connect_calendar}
        </button>
      </div>
    </div>
  );
}

function DoneStep({
  locale,
  t,
  custom,
  dbLabel,
  modelSummary,
  stt,
  tts,
  roundTrip,
}: {
  locale: Locale;
  t: InstallDictionary;
  custom: boolean;
  dbLabel: string;
  modelSummary: string;
  stt: string;
  tts: string;
  roundTrip: number;
}) {
  const summary = [
    {
      id: "reachable",
      label: t.sum_reachable,
      value: custom ? `https://${MACHINE.hostname}:8443` : "https://localhost:8443",
    },
    { id: "database", label: t.sum_database, value: dbLabel },
    { id: "model", label: t.sum_model, value: modelSummary },
    { id: "hearing", label: t.sum_hearing, value: stt },
    { id: "voice", label: t.sum_voice, value: tts },
    { id: "delay", label: t.sum_delay, value: `~${roundTrip} ms` },
    { id: "signedin", label: t.sum_signed_in, value: MACHINE.adminEmail },
  ];

  return (
    <div>
      <div className="inline-flex items-center gap-2 rounded-full border border-[color:var(--od-green-border)] bg-[rgba(63,185,132,.10)] p-[5px_11px] text-[12.5px] font-semibold text-[color:var(--od-green-text)]">
        {t.ready}
      </div>
      <h1 className="text-od-text mt-4 mb-0 text-[26px] font-semibold tracking-[-0.02em]">
        {t.done_title}
      </h1>
      <p className="text-od-muted-4 mt-2 max-w-[62ch] text-pretty">{t.done_blurb}</p>

      {/* §A6.1: the payoff of the entire flow. Deliberately the largest thing on the screen. */}
      <div className="border-od-line bg-od-panel-deep-2 mt-[22px] flex flex-col items-center gap-4 rounded-[14px] border p-[40px_28px] text-center">
        <div className="mono ltr-data text-od-muted text-[15px]">{MACHINE.number}</div>
        <button
          type="button"
          className="border-[color:var(--od-violet-border)] bg-[color:var(--od-violet)] hover:bg-[color:var(--od-violet-2)] max-w-full cursor-pointer rounded-xl border p-[18px_40px] text-[21px] font-semibold tracking-[-0.01em] whitespace-normal text-white"
        >
          {t.call_yourself}
        </button>
        <div className="text-od-muted-4 max-w-[46ch] text-pretty">{t.done_call_note}</div>
      </div>

      <Panel className="mt-5">
        {summary.map((row) => (
          <div
            key={row.id}
            className="flex flex-wrap items-center justify-between gap-x-5 gap-y-[10px] border-b border-[color:var(--od-raise-6)] p-[13px_18px]"
          >
            <span className="text-od-muted-4">{row.label}</span>
            <span
              dir="ltr"
              className="mono ltr-data text-od-text-2 text-start text-[12.5px] [overflow-wrap:anywhere]"
            >
              {row.value}
            </span>
          </div>
        ))}
      </Panel>

      <div className="mt-5 flex flex-wrap gap-[10px]">
        <Link
          href={`/${locale}/home`}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 inline-flex items-center rounded-lg border p-[11px_18px] font-semibold whitespace-nowrap"
        >
          {t.go_dashboard}
        </Link>
        <Link
          href={`/${locale}/numbers`}
          className="border-od-border-2 text-od-muted hover:text-od-text-2 hover:bg-[var(--od-raise-4)] inline-flex items-center rounded-lg border p-[11px_18px] whitespace-nowrap"
        >
          {t.add_number}
        </Link>
      </div>
    </div>
  );
}
