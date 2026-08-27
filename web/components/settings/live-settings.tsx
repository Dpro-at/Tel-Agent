"use client";

import { useState } from "react";

import { allSettings, saveSettings, type SettingRow } from "@/lib/api";
import { useResource } from "@/lib/use-resource";

/**
 * The settings that actually exist, edited against the server.
 *
 * Every other panel on the settings screen is still a drawing. This one is not, and
 * the difference has to be visible: a form that looks identical to a mock but silently
 * saves nothing is worse than one that is obviously unfinished.
 *
 * **A secret is rendered as its mask and submitted unchanged unless it is retyped.**
 * The server returns `••••3ab1`, ignores a write that sends the mask back, and reports
 * which keys it ignored. That is what lets the whole form be submitted at once without
 * the password field overwriting a live credential with bullets.
 */
export type FieldCopy = {
  key: string;
  label: string;
  help?: string;
};

export function LiveSettings({
  fields,
  labels,
  onSaved,
}: {
  fields: FieldCopy[];
  labels: {
    save: string;
    saving: string;
    saved: string;
    unchanged: string;
    loading: string;
    failed: string;
    retry: string;
    secretKept: string;
  };
  onSaved?: () => void;
}) {
  const settings = useResource<SettingRow[]>(() => allSettings());
  // Only what the person actually touched. Submitting the whole form would send back
  // every masked secret and every default, and "written" would then be meaningless.
  const [edits, setEdits] = useState<Record<string, string | number | boolean | null>>({});
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<{ text: string; bad?: boolean } | null>(null);

  if (settings.loading && settings.data === null) {
    return <p className="text-od-muted-5 p-[14px_18px] text-[13px]">{labels.loading}</p>;
  }

  if (settings.data === null) {
    return (
      <div className="p-[14px_18px]">
        <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
          {settings.error?.message ?? labels.failed}
        </p>
        <button
          type="button"
          onClick={settings.reload}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
        >
          {labels.retry}
        </button>
      </div>
    );
  }

  const rows = settings.data;
  const byKey = new Map(rows.map((row) => [row.key, row]));
  const dirty = Object.keys(edits).length > 0;

  function set(key: string, value: string | number | boolean | null) {
    setEdits((current) => ({ ...current, [key]: value }));
    setResult(null);
  }

  async function submit() {
    setSaving(true);
    setResult(null);
    try {
      const response = await saveSettings(edits);
      setEdits({});
      settings.reload();
      onSaved?.();
      const ignored = response.ignored_masked.length;
      setResult({
        text: ignored > 0 ? `${labels.saved} ${labels.secretKept}` : labels.saved,
      });
    } catch (thrown) {
      setResult({
        text: thrown instanceof Error ? thrown.message : String(thrown),
        bad: true,
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {fields.map((field, index) => {
        const row = byKey.get(field.key);
        if (!row) return null;
        const edited = Object.prototype.hasOwnProperty.call(edits, field.key);
        const current = edited ? edits[field.key] : row.value;

        return (
          <div
            key={field.key}
            className={`flex flex-wrap items-start justify-between gap-x-6 gap-y-3 p-[14px_18px] ${
              index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
            }`}
          >
            <div className="min-w-[200px] flex-[1_1_240px]">
              <label
                className="text-od-text-3 block font-medium text-pretty"
                htmlFor={`setting-${field.key}`}
              >
                {field.label}
              </label>
              <div className="text-od-muted-5 mt-1 max-w-[52ch] text-[12.5px] text-pretty">
                {field.help ?? row.description}
              </div>
            </div>
            <div className="min-w-[min(100%,240px)] flex-[0_1_300px]">
              {row.kind === "boolean" ? (
                <button
                  id={`setting-${field.key}`}
                  type="button"
                  role="switch"
                  aria-checked={current === true}
                  onClick={() => set(field.key, !(current === true))}
                  className="inline-flex h-[22px] w-10 cursor-pointer items-center rounded-full border p-[2px]"
                  style={{
                    borderColor: current === true ? "var(--od-violet)" : "var(--od-border-7)",
                    background: current === true ? "var(--od-violet)" : "var(--od-raise)",
                    justifyContent: current === true ? "flex-end" : "flex-start",
                  }}
                >
                  <span
                    className="size-4 rounded-full"
                    style={{ background: current === true ? "#fff" : "var(--od-stroke-5)" }}
                  />
                </button>
              ) : (
                <input
                  id={`setting-${field.key}`}
                  dir="ltr"
                  // A host, a port and a path are all machine values: they read left to
                  // right whichever way the page does.
                  className="mono ltr-data bg-od-canvas-2 border-od-border-6 text-od-text-2 w-full rounded-[7px] border p-[9px_12px] text-[13px] [overflow-wrap:anywhere]"
                  type={row.kind === "integer" ? "number" : "text"}
                  // Not `type="password"`: the value here is already a mask from the
                  // server, and hiding a row of bullets behind more bullets tells the
                  // person nothing about whether a password is set at all.
                  value={current === null || current === undefined ? "" : String(current)}
                  placeholder={row.secret ? "••••" : ""}
                  onChange={(event) => {
                    const raw = event.target.value;
                    if (row.kind === "integer") {
                      set(field.key, raw === "" ? null : Number(raw));
                    } else {
                      set(field.key, raw === "" ? null : raw);
                    }
                  }}
                />
              )}
            </div>
          </div>
        );
      })}

      <div className="flex flex-wrap items-center justify-between gap-x-[18px] gap-y-3 border-t border-[color:var(--od-raise-6)] p-[14px_18px]">
        <div
          className="max-w-[60ch] text-[13px] text-pretty"
          style={{
            color: result?.bad ? "var(--od-red-text-6)" : "var(--od-muted-5)",
          }}
        >
          {result ? result.text : dirty ? "" : labels.unchanged}
        </div>
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={submit}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? labels.saving : labels.save}
        </button>
      </div>
    </>
  );
}
