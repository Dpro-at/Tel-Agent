"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import {
  ADDRESS_FOR,
  CONNECTORS,
  CONNECTOR_KINDS,
  FOUND_TOOLS,
  HANDSHAKE,
  type Connector,
  type Tool,
} from "@/lib/connectors/data";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";

import type { ConnectorsDictionary } from "./page";

function statusTone(status: Connector["status"]) {
  if (status === "failing") {
    return {
      border: "var(--od-red-border)",
      background: "var(--od-red-bg)",
      color: "var(--od-red-text)",
    };
  }
  if (status === "connected") {
    return {
      border: "var(--od-green-border)",
      background: "var(--od-panel-green)",
      color: "var(--od-green-text)",
    };
  }
  return {
    border: "var(--od-border-7)",
    background: "var(--od-raise-5)",
    color: "var(--od-muted-5)",
  };
}

function ToolKind({ t, kind }: { t: ConnectorsDictionary; kind: Tool["kind"] }) {
  const write = kind === "write";
  return (
    <span
      className="rounded-[5px] border p-[2px_8px] text-[10.5px] font-bold tracking-[.05em] uppercase whitespace-nowrap"
      style={{
        borderColor: write ? "var(--od-amber-border)" : "var(--od-border-7)",
        background: write ? "var(--od-amber-bg)" : "var(--od-raise-5)",
        color: write ? "var(--od-amber-text)" : "var(--od-muted-5)",
      }}
    >
      {kind === "write" ? t.kind_write : t.kind_read}
    </span>
  );
}

function Toggle({ on, onClick }: { on: boolean; onClick?: () => void }) {
  return (
    <span
      onClick={onClick}
      className="inline-flex h-[22px] w-10 flex-none cursor-pointer items-center self-center rounded-full border p-[2px]"
      style={{
        borderColor: on ? "var(--od-violet)" : "var(--od-border-7)",
        background: on ? "var(--od-violet)" : "var(--od-raise)",
        justifyContent: on ? "flex-end" : "flex-start",
      }}
    >
      <span
        className="block size-4 rounded-full"
        style={{ background: on ? "#fff" : "var(--od-stroke-5)" }}
      />
    </span>
  );
}

export function Connectors({ locale, t }: { locale: Locale; t: ConnectorsDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [off, setOff] = useState<Record<string, boolean>>({});

  const empty = state === "empty";
  const loading = state === "loading";
  const current = CONNECTORS.find((entry) => entry.id === openId) ?? null;
  const failing = CONNECTORS.find((entry) => entry.status === "failing");

  /** A count that is worded needs its number putting back in. */
  const statOf = (tool: Tool) =>
    tool.statCount === undefined
      ? t[tool.stat]
      : interpolate(t[tool.stat], { count: tool.statCount });
  /** Arabic has a dual, so two is its own form rather than a plural with a 2 in it. */
  const usedByOf = (connector: Connector) => {
    if (connector.usedByCount === undefined) return t[connector.usedBy];
    if (connector.usedByCount === 2) return t.used_by_two;
    return interpolate(t[connector.usedBy], { count: connector.usedByCount });
  };

  const isOn = (connectorId: string, tool: Tool) => {
    const key = `${connectorId}.${tool.name}`;
    return key in off ? off[key] : tool.on;
  };
  const flip = (connectorId: string, tool: Tool) =>
    setOff((current) => ({ ...current, [`${connectorId}.${tool.name}`]: !isOn(connectorId, tool) }));

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="settings" />
      </div>

      <StatePreview state={state} onChange={setState} states={["default", "empty", "loading"]} />

      <div className="mx-auto max-w-[1080px] p-[26px_28px_90px]">
        {current ? (
          <ConnectorDetail
            t={t}
            statOf={statOf}
            connector={current}
            isOn={isOn}
            onFlip={flip}
            onBack={() => setOpenId(null)}
          />
        ) : (
          <div>
            <div className="text-od-faint flex flex-wrap items-center gap-x-[10px] gap-y-[6px] text-[12.5px]">
              <Link href={`/${locale}/settings`} className="text-od-violet hover:underline">
                {t.breadcrumb_settings}
              </Link>
              <span>/</span>
              <span>{t.breadcrumb_connectors}</span>
            </div>

            <div className="mt-3 flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
              <div className="min-w-0 flex-[1_1_420px]">
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                {/* The two directions are named apart so neither is mistaken for the other. */}
                <p className="text-od-muted-4 mt-2 max-w-[74ch] text-pretty">
                  {t.intro_before}
                  <Link href={`/${locale}/settings`} className="text-od-violet hover:underline">
                    {t.intro_link}
                  </Link>
                  {t.intro_after}
                </p>
              </div>
              <div className="flex flex-wrap gap-[10px]">
                <button
                  type="button"
                  onClick={() => setAdding(true)}
                  className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13px] font-medium"
                >
                  {t.add_connector}
                </button>
                <Link
                  href={`/${locale}/apps`}
                  className="border-od-border-7 text-od-muted hover:text-od-text-2 inline-flex items-center rounded-[7px] border p-[9px_15px] text-[13px] hover:bg-[var(--od-raise-4)] hover:no-underline"
                >
                  {t.browse_catalogue}
                </Link>
              </div>
            </div>

            <div className="mt-5 flex flex-col gap-4">
              {!empty && !loading && failing ? (
                <div className="border-od-red-border bg-od-red-bg flex flex-wrap items-center gap-[14px] rounded-[10px] border p-[14px_16px]">
                  <span
                    className="size-[10px] flex-none rounded-full bg-[#F0605E]"
                    style={{ animation: "od-ring 1.6s ease-out infinite" }}
                  />
                  <div className="min-w-[240px] flex-[1_1_340px]">
                    <div className="text-[15px] font-semibold text-[color:var(--od-red-text)]">
                      {interpolate(t.is_refusing, { name: t[failing.name] })}
                    </div>
                    <div className="mt-[3px] text-pretty text-[color:var(--od-red-text-2)]">
                      {failing.error ? t[failing.error.body] : null}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setOpenId(failing.id)}
                    className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium text-[color:var(--od-red-text-3)]"
                  >
                    {t.open_connector}
                  </button>
                </div>
              ) : null}

              {loading ? (
                <div className="flex flex-col gap-3">
                  {[0, 1, 2, 3].map((index) => (
                    <div
                      key={index}
                      className="border-od-raise-12 h-[104px] rounded-xl border"
                      style={{
                        background:
                          "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
                        backgroundSize: "420px 100%",
                        animation: "od-shimmer 1.4s linear infinite",
                      }}
                    />
                  ))}
                </div>
              ) : null}

              {empty ? (
                <div className="border-od-stroke-3 bg-od-panel-deep-3 rounded-xl border border-dashed p-[44px_28px] text-center">
                  <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.empty_title}</h2>
                  <p className="text-od-muted-4 mx-auto mt-[10px] max-w-[56ch] text-pretty">
                    {t.empty_body}
                  </p>
                  <div className="mt-5 flex flex-wrap justify-center gap-[10px]">
                    <button
                      type="button"
                      onClick={() => setAdding(true)}
                      className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13px] font-medium"
                    >
                      {t.empty_add}
                    </button>
                    <Link
                      href={`/${locale}/apps`}
                      className="border-od-border-7 text-od-muted hover:text-od-text-2 inline-flex items-center rounded-[7px] border p-[9px_16px] text-[13px] hover:no-underline"
                    >
                      {t.empty_see}
                    </Link>
                  </div>
                </div>
              ) : null}

              {!empty && !loading ? (
                <>
                  <div className="flex flex-col gap-[10px]">
                    {CONNECTORS.map((connector) => {
                      const tone = statusTone(connector.status);
                      const enabled = connector.tools.filter((tool) => isOn(connector.id, tool)).length;
                      return (
                        <div
                          key={connector.id}
                          onClick={() => setOpenId(connector.id)}
                          className="border-od-line bg-od-panel-deep-3 hover:border-od-stroke cursor-pointer rounded-xl border p-[15px_16px] hover:bg-[var(--od-raise-4)]"
                        >
                          <div className="flex flex-wrap items-start gap-x-[18px] gap-y-3">
                            <span className="border-od-border-9 text-od-text-5 inline-flex size-[38px] flex-none items-center justify-center rounded-lg border bg-[var(--od-raise-5)] font-semibold">
                              {connector.mark}
                            </span>
                            <div className="min-w-0 flex-[1_1_300px]">
                              <div className="flex flex-wrap items-center gap-[9px]">
                                <span className="text-od-text text-[15.5px] font-semibold text-pretty">
                                  {t[connector.name]}
                                </span>
                                <span
                                  className="rounded-full border p-[3px_9px] text-[11.5px] font-medium whitespace-nowrap"
                                  style={{
                                    borderColor: tone.border,
                                    background: tone.background,
                                    color: tone.color,
                                  }}
                                >
                                  {t[`status_${connector.status}`]}
                                </span>
                                <span className="border-od-border-7 text-od-muted-5 rounded-full border bg-[var(--od-raise-5)] p-[3px_9px] text-[11.5px] whitespace-nowrap">
                                  {t[`origin_${connector.origin}`]}
                                </span>
                              </div>
                              <div
                                dir="ltr"
                                className="mono ltr-data text-od-muted-5 mt-[5px] text-[12.5px] [overflow-wrap:anywhere]"
                              >
                                {connector.transport}
                              </div>
                              <div className="text-od-muted-2 mt-[6px] max-w-[70ch] text-pretty">
                                {t[connector.desc]}
                              </div>
                            </div>
                            <div className="flex min-w-[130px] flex-none flex-col items-end gap-[6px]">
                              <span className="text-od-muted-4 text-[12.5px]">
                                {interpolate(t.tools_on, {
                                  on: enabled,
                                  total: connector.tools.length,
                                })}
                              </span>
                              <span className="text-od-faint text-[12.5px]">{usedByOf(connector)}</span>
                            </div>
                            <span className="text-od-faint-2 flex-none self-center text-[15px]">›</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Tel-Agent does not vouch for third-party servers, and says so. */}
                  <div className="border-od-amber-border bg-od-amber-bg flex flex-wrap items-start gap-x-[18px] gap-y-3 rounded-[10px] border p-[15px_16px]">
                    <span className="flex-none font-semibold text-[color:var(--od-amber)]">!</span>
                    <div className="min-w-0 flex-[1_1_320px] text-[12.5px] text-pretty text-[color:var(--od-amber-text-3)]">
                      {t.warning_note}
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          </div>
        )}
      </div>

      {adding ? <AddConnectorDialog t={t} onClose={() => setAdding(false)} /> : null}
    </div>
  );
}

function ConnectorDetail({
  t,
  statOf,
  connector,
  isOn,
  onFlip,
  onBack,
}: {
  t: ConnectorsDictionary;
  statOf: (tool: Tool) => string;
  connector: Connector;
  isOn: (id: string, tool: Tool) => boolean;
  onFlip: (id: string, tool: Tool) => void;
  onBack: () => void;
}) {
  const tone = statusTone(connector.status);

  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        className="border-od-border-2 text-od-muted-4 hover:bg-od-raise hover:text-od-text-2 inline-flex cursor-pointer items-center gap-2 rounded-[7px] border bg-transparent p-[5px_11px_5px_8px] text-[13px]"
      >
        ← {t.back_all}
      </button>

      <div className="mt-4 flex flex-wrap items-start justify-between gap-x-6 gap-y-[14px]">
        <div className="min-w-0 flex-[1_1_380px]">
          <div className="flex flex-wrap items-center gap-[10px]">
            <h1 className="text-od-text m-0 text-[24px] font-semibold tracking-[-0.02em]">
              {t[connector.name]}
            </h1>
            <span
              className="rounded-full border p-[3px_9px] text-[11.5px] font-medium whitespace-nowrap"
              style={{ borderColor: tone.border, background: tone.background, color: tone.color }}
            >
              {t[`status_${connector.status}`]}
            </span>
          </div>
          <div
            dir="ltr"
            className="mono ltr-data text-od-muted-5 mt-[7px] text-[13px] [overflow-wrap:anywhere]"
          >
            {connector.transport}
          </div>
          <p className="text-od-muted-4 mt-[9px] max-w-[68ch] text-pretty">{t[connector.desc]}</p>
        </div>
        <div className="flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium"
          >
            {t.reconnect}
          </button>
          <button
            type="button"
            className="border-od-border-7 text-od-muted hover:border-od-red-border cursor-pointer rounded-[7px] border bg-transparent p-[8px_14px] text-[13px] hover:text-[color:var(--od-red-text)]"
          >
            {t.remove}
          </button>
        </div>
      </div>

      {connector.error ? (
        <div className="border-od-red-border bg-od-red-bg mt-[18px] rounded-[10px] border p-[14px_16px]">
          <div className="font-semibold text-[color:var(--od-red-text)]">
            {t[connector.error.title]}
          </div>
          <div className="mt-1 max-w-[74ch] text-pretty text-[color:var(--od-red-text-2)]">
            {t[connector.error.body]}
          </div>
          <div
            dir="ltr"
            className="mono ltr-data border-od-red-border-2 bg-od-red-bg-2 mt-2 rounded-[7px] border p-[10px_12px] text-[12.5px] [overflow-wrap:anywhere] text-[color:var(--od-red-text-3)]"
          >
            {connector.error.log}
          </div>
        </div>
      ) : null}

      <div
        className="mt-[18px] grid gap-[10px]"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}
      >
        {connector.stats.map((stat) => (
          <div
            key={stat.label}
            className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[13px_15px]"
          >
            <div className="text-[11px] tracking-[.09em] uppercase text-[color:var(--od-faint-5)]">
              {t[stat.label]}
            </div>
            {/* A measured figure keeps its own direction; a worded one follows the page. */}
            <div className="text-od-text mt-[6px] text-[17px] font-semibold text-pretty">
              {stat.value ? (
                t[stat.value]
              ) : (
                <span dir="ltr" className="ltr-data">
                  {stat.valueText}
                </span>
              )}
            </div>
            <div className="text-od-faint mt-[3px] text-[12px]">
              {stat.note ? (
                t[stat.note]
              ) : (
                <span dir="ltr" className="mono ltr-data">
                  {stat.noteText}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="border-od-line bg-od-panel-deep-3 mt-4 rounded-[10px] border">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2 p-[16px_18px_12px]">
          <h2 className="text-od-text m-0 text-[15px] font-semibold">{t.tools_heading}</h2>
          <span className="text-od-faint text-[12.5px] text-pretty">{t.tools_note}</span>
        </div>
        {connector.tools.map((tool) => (
          <div
            key={tool.name}
            className="flex flex-wrap items-start gap-x-4 gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[14px_18px]"
          >
            <div className="min-w-[220px] flex-[1_1_280px]">
              <div className="flex flex-wrap items-center gap-[9px]">
                <span dir="ltr" className="mono ltr-data text-od-text-3 text-[13px]">
                  {tool.name}
                </span>
                <ToolKind t={t} kind={tool.kind} />
              </div>
              <div className="text-od-muted-5 mt-1 max-w-[62ch] text-[12.5px] text-pretty">
                {t[tool.desc]}
              </div>
            </div>
            <span className="text-od-faint-2 flex-none self-center text-[12px]">{statOf(tool)}</span>
            <Toggle on={isOn(connector.id, tool)} onClick={() => onFlip(connector.id, tool)} />
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-start gap-4">
        <div className="border-od-line bg-od-panel-deep-3 min-w-[260px] flex-[1_1_320px] rounded-[10px] border">
          <div className="text-od-text p-[16px_18px_10px] text-[15px] font-semibold">
            {t.assistants_heading}
          </div>
          {connector.assistants.length === 0 ? (
            <div className="text-od-faint border-t border-[color:var(--od-raise-6)] p-[12px_18px] text-[12.5px]">
              {t.assistants_none}
            </div>
          ) : (
            connector.assistants.map((entry) => (
              <div
                key={entry.name}
                className="flex flex-wrap items-center gap-x-[14px] gap-y-2 border-t border-[color:var(--od-raise-6)] p-[12px_18px]"
              >
                <span className="text-od-text-3 min-w-0 flex-[1_1_160px]">{entry.name}</span>
                <span dir="ltr" className="mono ltr-data text-od-faint text-start text-[12.5px]">
                  {entry.tools}
                </span>
              </div>
            ))
          )}
        </div>

        <div className="border-od-line bg-od-panel-deep-3 min-w-[260px] flex-[1_1_320px] rounded-[10px] border">
          <div className="text-od-text p-[16px_18px_10px] text-[15px] font-semibold">
            {t.log_heading}
          </div>
          {connector.log.length === 0 ? (
            <div className="text-od-faint border-t border-[color:var(--od-raise-6)] p-[12px_18px] text-[12.5px]">
              {t.log_none}
            </div>
          ) : (
            connector.log.map((row, index) => {
              const failed = row.outcome === "failed";
              const thin = row.outcome === "empty" || row.outcome === "notFound";
              return (
                <div
                  key={index}
                  className="flex flex-wrap items-baseline gap-x-[14px] gap-y-2 border-t border-[color:var(--od-raise-6)] p-[11px_18px]"
                >
                  <span className="text-od-faint-2 flex-none text-[12px]">
                    {row.timeKey ? (
                      t[row.timeKey]
                    ) : (
                      <span dir="ltr" className="mono ltr-data">
                        {row.time}
                      </span>
                    )}
                  </span>
                  <span
                    dir="ltr"
                    className="mono ltr-data text-od-muted-2 min-w-0 flex-[1_1_130px] text-start text-[12.5px] [overflow-wrap:anywhere]"
                  >
                    {row.tool}
                  </span>
                  <span
                    className="rounded-[5px] border p-[1px_8px] text-[11.5px] font-medium whitespace-nowrap"
                    style={{
                      borderColor: failed
                        ? "var(--od-red-border)"
                        : thin
                          ? "var(--od-border-7)"
                          : "var(--od-green-border)",
                      background: failed
                        ? "rgba(240,96,94,.10)"
                        : thin
                          ? "transparent"
                          : "rgba(63,185,132,.10)",
                      color: failed
                        ? "var(--od-red-text-4)"
                        : thin
                          ? "var(--od-faint)"
                          : "var(--od-green-text)",
                    }}
                  >
                    {t[`out_${row.outcome}`]}
                  </span>
                  <span dir="ltr" className="mono ltr-data text-od-faint flex-none text-[12px]">
                    {row.ms}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

function AddConnectorDialog({ t, onClose }: { t: ConnectorsDictionary; onClose: () => void }) {
  const [step, setStep] = useState(1);
  const [kind, setKind] = useState("stdio");
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [auth, setAuth] = useState("none");
  const [enabled, setEnabled] = useState<Record<string, boolean>>({});

  const titles = [t.step1_title, t.step2_title, t.step3_title];

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center overflow-auto p-[40px_20px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div
        className="border-od-border-9 bg-od-panel w-full max-w-[620px] overflow-hidden rounded-[13px] border"
        style={{ boxShadow: "0 24px 60px var(--od-scrim-3)" }}
      >
        <div className="border-od-border flex flex-wrap items-start justify-between gap-x-4 gap-y-[10px] border-b p-[18px_20px_14px]">
          <div className="min-w-0 flex-[1_1_300px]">
            <div className="text-od-faint text-[11px] tracking-[.09em] uppercase">
              {interpolate(t.step_of, { step })}
            </div>
            <h2 className="text-od-text mt-[6px] mb-0 text-[19px] font-semibold text-pretty">
              {titles[step - 1]}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t.close}
            className="border-od-border-2 text-od-muted-4 hover:bg-od-raise hover:text-od-text size-7 flex-none cursor-pointer rounded-lg border bg-transparent text-[15px] leading-none"
          >
            ×
          </button>
        </div>

        {step === 1 ? (
          <div className="flex flex-col gap-[18px] p-[18px_20px]">
            <label className="flex flex-col gap-[6px]">
              <span className="text-od-text-3 text-[13px] font-medium">{t.dlg_name}</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t.dlg_name_placeholder}
                className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[10px_12px] text-[14px]"
              />
              <span className="text-od-faint text-[12px] text-pretty">{t.dlg_name_help}</span>
            </label>

            <div className="flex flex-col gap-2">
              <span className="text-od-text-3 text-[13px] font-medium">{t.dlg_where}</span>
              {CONNECTOR_KINDS.map((entry) => {
                const on = kind === entry.id;
                return (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => {
                      setKind(entry.id);
                      setAddress("");
                    }}
                    className="flex cursor-pointer items-start gap-[11px] rounded-[9px] border p-[12px_14px] text-start"
                    style={{
                      borderColor: on ? "var(--od-violet)" : "var(--od-border-7)",
                      background: on ? "var(--od-raise-10)" : "var(--od-canvas-2)",
                    }}
                  >
                    <span
                      className="mt-[3px] size-[15px] flex-none rounded-full border"
                      style={{
                        borderColor: on ? "var(--od-violet)" : "var(--od-stroke-5)",
                        background: on ? "var(--od-violet)" : "transparent",
                        boxShadow: on ? "inset 0 0 0 3px var(--od-panel)" : "none",
                      }}
                    />
                    <span className="min-w-0 flex-[1_1_auto] text-start">
                      <span className="text-od-text-2 block text-[14px] font-medium">
                        {t[entry.label]}
                      </span>
                      <span className="text-od-faint mt-[2px] block text-[12.5px] text-pretty">
                        {t[entry.note]}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            <label className="flex flex-col gap-[6px]">
              <span className="text-od-text-3 text-[13px] font-medium">
                {kind === "stdio" ? t.dlg_command : t.dlg_address}
              </span>
              <input
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                placeholder={ADDRESS_FOR[kind]}
                dir="ltr"
                className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[10px_12px] text-[13px]"
              />
            </label>

            <div className="flex flex-wrap gap-3">
              <label className="flex flex-[1_1_180px] flex-col gap-[6px]">
                <span className="text-od-text-3 text-[13px] font-medium">{t.dlg_auth}</span>
                <select
                  value={auth}
                  onChange={(event) => setAuth(event.target.value)}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[10px_12px] text-[14px]"
                >
                  <option value="none">{t.auth_none}</option>
                  <option value="bearer">{t.auth_bearer}</option>
                  <option value="header">{t.auth_header}</option>
                </select>
              </label>
              {auth !== "none" ? (
                <label className="flex flex-[1_1_180px] flex-col gap-[6px]">
                  <span className="text-od-text-3 text-[13px] font-medium">{t.dlg_key}</span>
                  <input
                    type="password"
                    placeholder="••••••••••••"
                    dir="ltr"
                    className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[10px_12px] text-[13px]"
                  />
                </label>
              ) : null}
            </div>

            {kind === "hosted" ? (
              <div className="border-od-amber-border bg-od-amber-bg flex gap-[11px] rounded-[9px] border p-[13px_15px]">
                <span className="flex-none font-semibold text-[color:var(--od-amber)]">!</span>
                <span className="text-[12.5px] text-pretty text-[color:var(--od-amber-text-3)]">
                  {t.hosted_warn}
                </span>
              </div>
            ) : null}
          </div>
        ) : null}

        {step === 2 ? (
          <div className="flex flex-col gap-[14px] p-[18px_20px]">
            <div
              dir="ltr"
              className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-muted-2 rounded-lg border p-[12px_14px] text-[12.5px] [overflow-wrap:anywhere]"
            >
              {address || ADDRESS_FOR[kind]}
            </div>
            {HANDSHAKE.map((entry) => (
              <div key={entry.label} className="flex flex-wrap items-baseline gap-x-3 gap-y-2">
                <span className="w-4 flex-none text-[12px] font-semibold text-[color:var(--od-green-text)]">
                  ✓
                </span>
                <span className="text-od-text-2 min-w-0 flex-[1_1_200px] text-[13.5px] text-pretty">
                  {t[entry.label]}
                </span>
                <span className="text-od-faint text-[12px]">
                  {entry.detail ? (
                    t[entry.detail]
                  ) : (
                    <span dir="ltr" className="mono ltr-data">
                      {entry.detailText}
                    </span>
                  )}
                </span>
              </div>
            ))}
            <div className="border-od-green-border bg-od-panel-green rounded-[9px] border p-[13px_15px] text-[13px] text-pretty text-[color:var(--od-green-text)]">
              {t.handshake_ok}
            </div>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="flex flex-col gap-1 pt-[6px]">
            {/* Everything arrives off. That is the whole safety model of this screen. */}
            <div className="text-od-faint p-[12px_20px_10px] text-[12.5px] text-pretty">
              {t.step3_note}
            </div>
            {FOUND_TOOLS.map((tool) => (
              <div
                key={tool.name}
                className="flex flex-wrap items-start gap-x-[14px] gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[13px_20px]"
              >
                <div className="min-w-[200px] flex-[1_1_240px]">
                  <div className="flex flex-wrap items-center gap-[9px]">
                    <span dir="ltr" className="mono ltr-data text-od-text-3 text-[13px]">
                      {tool.name}
                    </span>
                    <ToolKind t={t} kind={tool.kind} />
                  </div>
                  <div className="text-od-muted-5 mt-1 max-w-[58ch] text-[12.5px] text-pretty">
                    {t[tool.desc]}
                  </div>
                </div>
                <Toggle
                  on={enabled[tool.name] ?? false}
                  onClick={() =>
                    setEnabled((current) => ({ ...current, [tool.name]: !current[tool.name] }))
                  }
                />
              </div>
            ))}
          </div>
        ) : null}

        <div className="border-od-border bg-od-panel-deep-2 flex flex-wrap items-center justify-between gap-[10px] border-t p-[15px_20px]">
          <button
            type="button"
            onClick={() => (step === 1 ? onClose() : setStep(step - 1))}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] text-[13.5px]"
          >
            {step === 1 ? t.cancel : t.back}
          </button>
          <button
            type="button"
            onClick={() => (step === 3 ? onClose() : setStep(step + 1))}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold"
          >
            {step === 1 ? t.step1_next : step === 2 ? t.step2_next : t.step3_next}
          </button>
        </div>
      </div>
    </div>
  );
}
