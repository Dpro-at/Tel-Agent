"use client";

import { useState } from "react";

import {
  ApiError,
  genericChannel,
  saveGenericChannel,
  testGenericChannel,
  type ChannelField,
  type GenericChannel,
} from "@/lib/api";
import { useResource } from "@/lib/use-resource";

/**
 * One settings card, drawn from the channel's own descriptor.
 *
 * Modelled on the Discord card, which is the contract every channel card keeps: a
 * title, the one sentence that says what this channel is, a link to the platform's
 * own setup guide, a box per credential, and the four things an operator does —
 * save, test, switch on, remove.
 *
 * What is different is that none of it is written per channel. The fields come from
 * `setup.fields`, the words come from the descriptor unless a locale key overrides
 * them, and a secret is a `password` box whose placeholder is the masked preview of
 * what is already stored — so "a token is saved, leave this empty to keep it" is
 * shown rather than explained.
 */

/** The dictionary is a wide object of strings; a per-field key is looked up by name. */
type Words = Record<string, string>;

function copy(words: Words, key: string, fallback: string): string {
  const found = words[key];
  return typeof found === "string" && found !== "" ? found : fallback;
}

function fieldLabel(words: Words, kind: string, field: ChannelField): string {
  return copy(words, `ch_${kind}_${field.name}`, field.label);
}

function fieldHelp(words: Words, kind: string, field: ChannelField): string {
  return copy(words, `ch_${kind}_${field.name}_help`, field.help);
}

const inputClass =
  "border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] " +
  "border p-[9px_11px] text-start text-[13px]";

const buttonClass =
  "border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer " +
  "rounded-[7px] border p-[8px_14px] text-[13px] font-semibold " +
  "disabled:cursor-not-allowed disabled:opacity-50";

function FieldBox({
  words,
  kind,
  field,
  row,
  value,
  onChange,
}: {
  words: Words;
  kind: string;
  field: ChannelField;
  row: GenericChannel;
  value: string;
  onChange: (next: string) => void;
}) {
  const saved = field.secret ? row.previews[field.name] : null;
  const help = fieldHelp(words, kind, field);
  const shared = {
    dir: "ltr" as const,
    value,
    onChange: (event: { target: { value: string } }) => onChange(event.target.value),
    placeholder: field.secret ? (saved ?? field.placeholder) : field.placeholder,
    className: inputClass,
  };

  return (
    <label className="min-w-[240px] flex-[1_1_320px]">
      <span className="text-od-text-3 text-[13px] font-medium">
        {fieldLabel(words, kind, field)}
      </span>
      {field.multiline ? (
        <textarea {...shared} rows={4} />
      ) : (
        <input {...shared} type={field.secret ? "password" : "text"} />
      )}
      <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
        {saved ? copy(words, "ch_keep", "A value is saved.") : help}
      </span>
    </label>
  );
}

export function ChannelCard({ kind, t }: { kind: string; t: object }) {
  const words = t as Words;
  const channel = useResource<GenericChannel>(() => genericChannel(kind), [kind]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const row = channel.data;

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "encryption_key_missing") return words.ch_error_no_key;
      if (thrown.code === "missing_credentials") return words.ch_error_missing;
      if (thrown.code === `${kind}_refused`) return words.ch_error_refused;
      return thrown.message;
    }
    return String(thrown);
  };

  const act = async (run: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    setTested(null);
    try {
      await run();
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  if (row === null) return null;

  const required = row.setup.fields.filter((field) => field.required);
  const optional = row.setup.fields.filter((field) => !field.required);
  const stored = row.setup.fields.some(
    (field) => row.previews[field.name] || row.values[field.name],
  );
  const filled = Object.values(edits).some((value) => value.trim() !== "");
  // What the platform last said this account is - the test's answer while it is
  // fresh, and the remembered one otherwise.
  const knownAs = tested || row.identity;

  const box = (field: ChannelField) => (
    <FieldBox
      key={field.name}
      words={words}
      kind={kind}
      field={field}
      row={row}
      value={edits[field.name] ?? ""}
      onChange={(next) => setEdits((was) => ({ ...was, [field.name]: next }))}
    />
  );

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {row.setup.title}
        </h3>
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
          {row.setup.note}{" "}
          <a
            href={row.setup.guide_url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-od-text-3 underline"
          >
            {words.ch_guide}
          </a>
        </span>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-3">{required.map(box)}</div>

      {optional.length > 0 ? (
        <div className="mt-4">
          <span className="text-od-faint-2 text-[12px] tracking-[.06em] uppercase">
            {words.ch_optional}
          </span>
          <div className="mt-2 flex flex-wrap items-end gap-3">{optional.map(box)}</div>
        </div>
      ) : null}

      {row.webhook_url ? (
        <div className="mt-4">
          <span className="text-od-text-3 text-[13px] font-medium">{words.ch_webhook_url}</span>
          <div className="mt-2 flex items-center gap-3">
            <input dir="ltr" readOnly value={row.webhook_url} className={`${inputClass} mt-0`} />
            <button
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(row.webhook_url ?? "");
                  setCopied(true);
                } catch {
                  // A denied clipboard is not an error worth a red box - the address
                  // is on screen and can be selected.
                  setCopied(false);
                }
              }}
              className={`${buttonClass} whitespace-nowrap`}
            >
              {copied ? words.wc_copied : words.wc_copy}
            </button>
          </div>
          <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
            {words.ch_webhook_help}
          </span>
        </div>
      ) : null}

      <div className="border-od-border mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button
          type="button"
          disabled={busy || !filled}
          onClick={() =>
            void act(async () => {
              const fields = Object.fromEntries(
                Object.entries(edits).map(([name, value]) => [name, value.trim()]),
              );
              await saveGenericChannel(kind, { fields });
              setEdits({});
            })
          }
          className={buttonClass}
        >
          {busy ? words.ch_saving : words.ch_save}
        </button>
        <button
          type="button"
          disabled={busy || !stored}
          onClick={() =>
            void act(async () => {
              const answer = await testGenericChannel(kind);
              setTested(answer.identity ?? "");
            })
          }
          className={buttonClass}
        >
          {words.ch_test}
        </button>
        <button
          type="button"
          disabled={busy || !stored}
          onClick={() => void act(() => saveGenericChannel(kind, { enabled: !row.enabled }))}
          className={buttonClass}
        >
          {words.ch_enabled}
          {row.enabled ? " ✓" : ""}
        </button>
        {stored ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(() =>
                saveGenericChannel(kind, {
                  fields: Object.fromEntries(
                    row.setup.fields.map((field) => [field.name, ""]),
                  ),
                }),
              )
            }
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[12.5px]"
          >
            {words.ch_clear}
          </button>
        ) : null}
        <span className="text-od-faint ms-auto max-w-[44ch] text-[12.5px] text-pretty">
          {knownAs ? `${words.ch_identity} ${knownAs}` : row.verified_live ? "" : words.ch_unverified}
        </span>
      </div>

      {problem !== null ? (
        <div className="mt-3 max-w-[62ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
          {problem}
        </div>
      ) : saved ? (
        <div className="text-od-muted-5 mt-3 text-[13px]">{words.wc_saved}</div>
      ) : null}
    </div>
  );
}
