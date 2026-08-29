"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  GROUPS,
  PANEL_META,
  PROMPT_TEMPLATES,
  TECHNICAL,
  type PanelId,
  type RailRow,
} from "@/lib/editor/data";
import { assistant as fetchAssistant, changeAssistant, type Assistant } from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { EditorDictionary } from "./page";

const ICONS: Record<string, string> = {
  cube: "M12 2 3 7v10l9 5 9-5V7l-9-5Z M3 7l9 5 9-5 M12 12v10",
  help: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z M9.1 9a3 3 0 1 1 4.5 2.6c-.9.5-1.6 1.2-1.6 2.4 M12 17.5h.01",
  forward: "M4 4h4l2 5-2.5 1.5a11 11 0 0 0 5.5 5.5L15 14l5 2v4a12 12 0 0 1-16-16Z M16 3h5v5",
  mail: "M3 6h18v12H3V6Z M3 7l9 6 9-6",
  contact: "M4 3h16v18H4V3Z M12 11a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z M8 17c0-2.2 1.8-3.5 4-3.5s4 1.3 4 3.5",
  spark: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z",
  sms: "M4 5h16v10H9l-5 4V5Z",
  calendar: "M4 6h16v15H4V6Z M4 10h16 M9 3v4 M15 3v4",
  webhook: "M9 8a3.5 3.5 0 1 1 5 3.2L11.5 16 M15.5 12.5a3.5 3.5 0 1 1-1 6.5H8 M8.5 12 6 16.5a3.5 3.5 0 1 0 4.5 4.5",
  plug: "M9 3v6 M15 3v6 M6 9h12v3a6 6 0 0 1-12 0V9Z M12 18v3",
};

function Icon({ name, color }: { name: string; color: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={17}
      height={17}
      fill="none"
      stroke={color}
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {(ICONS[name] ?? ICONS.cube).split(" M").map((segment, index) => (
        <path key={index} d={(index ? "M" : "") + segment} />
      ))}
    </svg>
  );
}

export function AssistantEditor({
  locale,
  t,
  assistantId,
}: {
  locale: Locale;
  t: EditorDictionary;
  assistantId: number;
}) {
  const [tab, setTab] = useState<"behaviour" | "technical">("behaviour");
  const [panel, setPanel] = useState<PanelId>("persona");

  const loaded = useResource<Assistant>(() => fetchAssistant(assistantId), [assistantId]);
  const row = loaded.data;

  /**
   * The panel being edited, held apart from the row it came from.
   *
   * A panel saves on its own - that is what the PATCH endpoint is shaped for - so the
   * draft has to survive a reload of the row without being overwritten by it, and has
   * to be replaced when a genuinely different assistant arrives.
   */
  const [draft, setDraft] = useState<Assistant | null>(null);
  const [baseline, setBaseline] = useState<Assistant | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  // Adjusted during render rather than in an effect. React re-runs this component
  // before touching the DOM, so the draft is right on the first paint instead of one
  // render late - and `use-resource.ts` gives the same reason for deriving `loading`.
  if (row !== baseline) {
    setBaseline(row);
    setDraft(row);
    setSavedAt(null);
    setProblem(null);
  }

  const edit = (fields: Partial<Assistant>) =>
    setDraft((current) => (current === null ? current : { ...current, ...fields }));

  /** Only the fields this panel owns, so a save never carries a panel nobody opened. */
  const save = async (fields: Parameters<typeof changeAssistant>[1]) => {
    if (draft === null || saving) return;
    setSaving(true);
    setProblem(null);
    try {
      await changeAssistant(draft.id, fields);
      setSavedAt(Date.now());
    } catch {
      setProblem(t.save_failed);
    } finally {
      setSaving(false);
    }
  };

  const meta = PANEL_META[panel];

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="assistants" />
      </div>

      <div className="mx-auto max-w-[1180px] p-[26px_28px_140px]">
        {loaded.error !== null && draft === null ? (
          <div className="border-od-red-border bg-od-red-bg rounded-[10px] border p-[22px_24px]">
            <h2 className="m-0 text-[17px] font-semibold text-[color:var(--od-red-text)]">
              {loaded.error.kind === "offline"
                ? t.error_offline_title
                : loaded.error.kind === "failed" && /404/.test(loaded.error.message)
                  ? t.not_found_title
                  : t.error_failed_title}
            </h2>
            <p className="mt-[8px] max-w-[62ch] text-pretty text-[color:var(--od-red-text-2)]">
              {loaded.error.message}
            </p>
            <div className="mt-4 flex flex-wrap gap-[10px]">
              <button
                type="button"
                onClick={loaded.reload}
                className="border-od-red-border bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[8px_14px] font-medium text-[color:var(--od-red-text)]"
              >
                {t.retry}
              </button>
              <Link
                href={`/${locale}/assistants`}
                className="border-od-line text-od-muted-4 hover:text-od-text-2 inline-block rounded-md border p-[8px_14px] hover:no-underline"
              >
                {t.back}
              </Link>
            </div>
          </div>
        ) : null}

        {draft === null && loaded.error === null ? (
          <div>
            <div
              className="h-[30px] w-[220px] rounded-md"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
            <div className="mt-[30px] flex flex-col gap-[10px]">
              {[0, 1, 2, 3, 4, 5, 6].map((index) => (
                <div
                  key={index}
                  className="border-od-raise-12 h-[58px] rounded-[10px] border"
                  style={{
                    background:
                      "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
                    backgroundSize: "420px 100%",
                    animation: "od-shimmer 1.4s linear infinite",
                  }}
                />
              ))}
            </div>
          </div>
        ) : null}

        {draft !== null ? (
          <div>
            <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-[14px]">
              <div className="flex min-w-0 items-start gap-3">
                <Link
                  href={`/${locale}/assistants`}
                  aria-label={t.back}
                  className="border-od-border-2 bg-od-panel text-od-muted-4 hover:bg-od-raise hover:text-od-text mt-[3px] inline-flex size-8 flex-none items-center justify-center rounded-lg border hover:no-underline"
                >
                  ←
                </Link>
                <div className="min-w-0">
                  <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                    {draft.name}
                  </h1>
                  <div className="text-od-muted-4 mt-[3px]">
                    {draft.role ?? interpolate(t.subtitle, { name: draft.name, business: "" }).trim()}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className="text-[13px]"
                  style={{
                    color: problem !== null ? "var(--od-red-text)" : "var(--od-muted-5)",
                  }}
                >
                  {problem ?? (saving ? t.saving : savedAt !== null ? t.saved : "")}
                </span>
                <button
                  type="button"
                  aria-label={t.more}
                  className="border-od-border-2 bg-od-panel text-od-muted-4 hover:bg-od-raise hover:text-od-text inline-flex size-8 cursor-pointer items-center justify-center rounded-lg border"
                >
                  ⋯
                </button>
              </div>
            </div>

            <div className="border-od-border mt-[22px] flex gap-1 border-b">
              {(
                [
                  ["behaviour", "tab_behaviour"],
                  ["technical", "tab_technical"],
                ] as const
              ).map(([id, label]) => {
                const on = tab === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setTab(id)}
                    className="-mb-px me-[18px] cursor-pointer border-none bg-transparent p-[10px_4px] text-[15px]"
                    style={{
                      fontWeight: on ? 600 : 400,
                      color: on ? "var(--od-text)" : "var(--od-muted-4)",
                      borderBottom: `2px solid ${on ? "var(--od-text-5)" : "transparent"}`,
                    }}
                  >
                    {t[label]}
                  </button>
                );
              })}
            </div>

            {tab === "behaviour" ? (
              <div className="mt-[26px] flex flex-wrap items-start gap-6">
                <div className="flex max-w-[340px] min-w-[min(100%,280px)] flex-[1_1_300px] flex-col gap-5">
                  {GROUPS.map((group) => (
                    <div key={group.id} className="flex flex-col gap-[10px]">
                      <div className="pb-[2px]">
                        <div className="text-od-faint text-[12px] font-semibold tracking-[.08em] uppercase">
                          {t[group.label]}
                        </div>
                        <div className="text-od-faint-2 mt-[3px] text-[12px] text-pretty">
                          {t[group.note]}
                        </div>
                      </div>

                      {group.rows.map((row) => (
                        <RailRowView
                          key={row.panel}
                          t={t}
                          row={row}
                          selected={panel === row.panel}
                          onOpen={() => setPanel(row.panel)}
                        />
                      ))}
                    </div>
                  ))}
                </div>

                <div className="min-w-[min(100%,420px)] flex-[3_1_460px]">
                  <div className="border-od-line bg-od-panel overflow-hidden rounded-xl border">
                    <div className="border-od-border flex flex-wrap items-start justify-between gap-x-4 gap-y-[10px] border-b p-[18px_20px_14px]">
                      <div className="min-w-0 flex-[1_1_240px]">
                        <h2 className="text-od-text m-0 text-[19px] font-semibold tracking-[-0.01em] text-pretty">
                          {t[meta.title]}
                        </h2>
                        <p className="text-od-muted-4 mt-[6px] max-w-[60ch] text-[13px] text-pretty">
                          {t[meta.blurb]}
                        </p>
                      </div>
                    </div>

                    {panel === "persona" ? (
                      <PersonaPanel
                        t={t}
                        draft={draft}
                        saving={saving}
                        onEdit={edit}
                        onSave={() =>
                          save({
                            name: draft.name,
                            persona: draft.persona,
                            language: draft.language,
                          })
                        }
                        onCancel={loaded.reload}
                      />
                    ) : null}

                    {panel === "instructions" ? (
                      <InstructionsPanel
                        t={t}
                        draft={draft}
                        saving={saving}
                        onEdit={edit}
                        onSave={() => save({ instructions: draft.instructions })}
                        onCancel={loaded.reload}
                      />
                    ) : null}
                    {panel !== "persona" && panel !== "instructions" ? (
                      <PendingPanel t={t} panel={panel} />
                    ) : null}
                  </div>
                </div>
              </div>
            ) : (
              <TechnicalTab t={t} />
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function RailRowView({
  t,
  row,
  selected,
  onOpen,
}: {
  t: EditorDictionary;
  row: RailRow;
  selected: boolean;
  onOpen: () => void;
}) {
  const enabled = row.enabled !== false;

  return (
    <div
      onClick={onOpen}
      className="hover:bg-od-raise flex cursor-pointer flex-nowrap items-center gap-3 rounded-[10px] p-[13px_15px]"
      style={{
        border: selected
          ? "1px solid var(--od-violet-border)"
          : enabled
            ? "1px solid var(--od-line)"
            : "1px dashed var(--od-border-7)",
        background: selected
          ? "rgba(139,124,255,.10)"
          : enabled
            ? "var(--od-panel-deep-3)"
            : "transparent",
      }}
    >
      <span
        className="inline-flex size-[30px] flex-none items-center justify-center rounded-lg border"
        style={{
          borderColor: enabled ? "var(--od-border-6)" : "transparent",
          background: enabled ? "var(--od-raise-5)" : "var(--od-raise)",
        }}
      >
        <Icon name={row.icon} color={enabled ? "var(--od-muted-4)" : "var(--od-faint-2)"} />
      </span>

      <div className="min-w-0 flex-[1_1_0]">
        <div className="flex flex-wrap items-center gap-[9px]">
          <span
            className="text-[15px] font-semibold text-pretty"
            style={{
              color: selected
                ? "var(--od-text)"
                : enabled
                  ? "var(--od-text-3)"
                  : "var(--od-muted-4)",
            }}
          >
            {t[row.title]}
          </span>
        </div>
        {row.hint ? (
          <div className="text-od-faint-2 mt-[3px] text-[12px] text-pretty">{t[row.hint]}</div>
        ) : null}
        {row.value || row.valueText ? (
          <div className="text-od-muted-5 mt-[4px] text-[12.5px]">
            {row.value ? t[row.value] : row.valueText}
          </div>
        ) : null}
      </div>

      {/* An unconfigured capability offers a green plus, not a chevron into an empty panel. */}
      {enabled ? (
        <span className="text-od-faint-2 w-[22px] flex-none text-center text-[18px] leading-none">
          ›
        </span>
      ) : (
        <span className="inline-flex size-[22px] flex-none items-center justify-center rounded-full bg-[color:var(--od-green)] text-[15px] leading-none font-bold text-[#08130E]">
          +
        </span>
      )}
    </div>
  );
}

function PanelFooter({
  t,
  saving,
  onSave,
  onCancel,
}: {
  t: EditorDictionary;
  saving: boolean;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="border-od-border flex flex-wrap justify-end gap-[10px] border-t pt-[14px]">
      <button
        type="button"
        onClick={onCancel}
        disabled={saving}
        className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50"
      >
        {t.cancel}
      </button>
      <button
        type="button"
        onClick={onSave}
        disabled={saving}
        className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 inline-flex cursor-pointer items-center gap-[9px] rounded-[7px] border p-[9px_16px] font-semibold whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50"
      >
        {saving ? t.saving : t.save}
      </button>
    </div>
  );
}

function PersonaPanel({
  t,
  draft,
  saving,
  onEdit,
  onSave,
  onCancel,
}: {
  t: EditorDictionary;
  draft: Assistant;
  saving: boolean;
  onEdit: (fields: Partial<Assistant>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="flex flex-col gap-5 p-[20px_22px_24px]">
      <label className="flex flex-col gap-[7px]">
        <span className="text-od-text-3 text-[13px] font-medium">{t.p_name}</span>
        <input
          value={draft.name}
          onChange={(event) => onEdit({ name: event.target.value })}
          className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[10px_12px] text-[14px]"
        />
      </label>

      <label className="flex max-w-[280px] flex-col gap-[7px]">
        <span className="text-od-text-3 text-[13px] font-medium">{t.p_language}</span>
        <select
          value={draft.language ?? ""}
          onChange={(event) => onEdit({ language: event.target.value || null })}
          className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[10px_12px] text-[14px]"
        >
          {/* The useful default, and the one the agent already does. */}
          <option value="">{t.p_language_auto}</option>
          <option value="de-AT">{t.lang_de_at}</option>
          <option value="de-DE">{t.lang_de_de}</option>
          <option value="en-GB">{t.lang_en_gb}</option>
        </select>
      </label>

      <label className="flex flex-col gap-[7px]">
        <span className="text-od-text-3 text-[13px] font-medium">{t.p_persona}</span>
        {/* Written by the customer, in the language their customers speak. */}
        <textarea
          dir="ltr"
          value={draft.persona}
          onChange={(event) => onEdit({ persona: event.target.value })}
          className="border-od-border-6 bg-od-canvas-2 text-od-text-2 min-h-[160px] w-full resize-y rounded-lg border p-[13px_14px] text-start text-[13.5px] leading-[1.7]"
        />
        <span className="text-od-faint max-w-[56ch] text-[12.5px] text-pretty">
          {t.p_persona_note}
        </span>
      </label>

      <PanelFooter t={t} saving={saving} onSave={onSave} onCancel={onCancel} />
    </div>
  );
}

function InstructionsPanel({
  t,
  draft,
  saving,
  onEdit,
  onSave,
  onCancel,
}: {
  t: EditorDictionary;
  draft: Assistant;
  saving: boolean;
  onEdit: (fields: Partial<Assistant>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="flex flex-col gap-4 p-[20px_24px_28px]">
      <div className="flex flex-wrap gap-2">
        {PROMPT_TEMPLATES.map((template) => (
          <button
            key={template.id}
            type="button"
            className="border-od-border-7 text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-full border bg-transparent p-[7px_13px] text-[13px] whitespace-nowrap"
            style={{
              borderColor:
                draft.template === template.id ? "var(--od-violet-border)" : undefined,
              color: draft.template === template.id ? "var(--od-violet-3)" : undefined,
            }}
          >
            {t[template.label]}
          </button>
        ))}
      </div>

      {/* The customer writes this in the language their customers speak. */}
      <textarea
        dir="ltr"
        value={draft.instructions}
        onChange={(event) => onEdit({ instructions: event.target.value })}
        className="border-od-border-6 bg-od-canvas-2 text-od-text-2 min-h-[300px] w-full resize-y rounded-lg border p-[15px_16px] text-start text-[13.5px] leading-[1.75]"
      />

      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">{t.i_note}</span>
        {/* The server's own ceiling, so the count means refusal rather than advice. */}
        <span dir="ltr" className="mono ltr-data text-od-faint-2 text-[12px]">
          {draft.instructions.length} / 8000
        </span>
      </div>

      <PanelFooter t={t} saving={saving} onSave={onSave} onCancel={onCancel} />
    </div>
  );
}

/**
 * A panel whose subsystem is not built. It says which one, and stops.
 *
 * The alternative was leaving the mock in place, which reads as a working screen that
 * silently discards every edit - the one failure worse than an obviously empty panel.
 */
function PendingPanel({ t, panel }: { t: EditorDictionary; panel: PanelId }) {
  const hints: Partial<Record<PanelId, keyof EditorDictionary>> = {
    contacts: "pending_contacts",
    knowledge: "pending_knowledge",
    booking: "pending_booking",
    forward: "pending_forward",
    apps: "pending_apps",
    webhooks: "pending_webhooks",
    email: "pending_email",
    sms: "pending_sms",
  };
  const hint = hints[panel];
  return (
    <div className="p-[26px_24px_30px]">
      <div className="border-od-border-7 rounded-[10px] border border-dashed p-[26px_22px]">
        <p className="text-od-muted-4 m-0 max-w-[56ch] text-pretty">
          {hint ? t[hint] : null}
        </p>
      </div>
    </div>
  );
}

function TechnicalTab({ t }: { t: EditorDictionary }) {
  return (
    <div className="border-od-line bg-od-panel-deep-3 mt-[26px] rounded-[10px] border">
      {TECHNICAL.map((row, index) => (
        <div
          key={row.id}
          className={`flex flex-wrap items-start justify-between gap-x-6 gap-y-3 p-[14px_18px] ${
            index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
          }`}
        >
          <div className="min-w-[200px] flex-[1_1_240px]">
            <div className="text-od-text-3 font-medium text-pretty">{t[row.label]}</div>
            <div className="text-od-muted-5 mt-1 max-w-[52ch] text-[12.5px] text-pretty">
              {t[row.help]}
            </div>
          </div>
          {/* A product name is data; the phrase after it is copy, so they are separate. */}
          <span className="border-od-border-6 bg-od-canvas-2 text-od-text-2 min-w-[min(100%,240px)] flex-[0_1_300px] rounded-[7px] border p-[9px_12px] text-[12.5px] [overflow-wrap:anywhere]">
            {row.valueKey ? (
              t[row.valueKey]
            ) : (
              <>
                <span className="mono">{row.valueText}</span>
                {row.suffix ? ` · ${t[row.suffix]}` : null}
              </>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
