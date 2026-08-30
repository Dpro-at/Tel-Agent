"use client";

import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  addKnowledge,
  ApiError,
  assistantsList,
  changeKnowledge,
  knowledgeList,
  KNOWLEDGE_CONTENT_MAX,
  removeKnowledge,
  type Assistant,
  type KnowledgeSource,
} from "@/lib/api";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { KnowledgeDictionary } from "./page";

/**
 * The design drew far more than a table: a website being crawled, a PDF being parsed,
 * an index rebuilding, opening hours, closures, and the questions callers asked that
 * nothing answered. Every one of those is its own subsystem, and none of them changes
 * what a piece of knowledge is - a title and some text this business wants answered
 * from. They land on top of this screen when they exist.
 *
 * The one line under the heading says the part a reader would otherwise assume: there
 * is no index yet, so the agent reads these in full.
 */
const COLUMNS = "minmax(0,2fr) minmax(130px,max-content) minmax(110px,max-content) max-content";

function edited(iso: string, locale: Locale): string {
  return new Date(iso).toLocaleDateString(locale === "ar" ? "ar" : locale, {
    day: "numeric",
    month: "short",
  });
}

type Draft = {
  id: number | null;
  title: string;
  content: string;
  assistant_id: number | null;
};

export function Knowledge({ locale, t }: { locale: Locale; t: KnowledgeDictionary }) {
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const list = useResource<KnowledgeSource[]>(() => knowledgeList(), []);
  // The dialog needs the names to offer; the list already carries the name of the
  // assistant each source belongs to, so this is only for the picker.
  const assistants = useResource<Assistant[]>(() => assistantsList(), []);

  const all = list.data ?? [];
  const needle = search.trim().toLowerCase();
  const rows = needle
    ? all.filter(
        (row) =>
          row.title.toLowerCase().includes(needle) ||
          row.content.toLowerCase().includes(needle),
      )
    : all;

  const remove = async (id: number) => {
    setBusy(true);
    try {
      await removeKnowledge(id);
      setConfirming(null);
      list.reload();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-od-canvas text-od-text-3 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="knowledge" />
      </div>

      <div className="mx-auto max-w-[1240px] p-[22px_28px_70px]">
        {list.error !== null && list.data === null ? (
          <div className="border-od-red-border bg-od-red-bg rounded-[10px] border p-[22px_24px]">
            <h2 className="m-0 text-[17px] font-semibold text-[color:var(--od-red-text)]">
              {list.error.kind === "offline" ? t.error_offline_title : t.error_failed_title}
            </h2>
            <p className="mt-[8px] max-w-[62ch] text-pretty text-[color:var(--od-red-text-2)]">
              {list.error.message}
            </p>
            <button
              type="button"
              onClick={list.reload}
              className="border-od-red-border bg-od-red-bg-2 hover:bg-od-red-bg-3 mt-4 cursor-pointer rounded-md border p-[8px_14px] font-medium text-[color:var(--od-red-text)]"
            >
              {t.retry}
            </button>
          </div>
        ) : null}

        {list.data !== null ? (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div className="max-w-[62ch]">
                <h1 className="text-od-text m-0 text-[24px] font-semibold tracking-[-0.015em]">
                  {t.title}
                </h1>
                <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
              </div>
              <button
                type="button"
                onClick={() =>
                  setDraft({ id: null, title: "", content: "", assistant_id: null })
                }
                className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13.5px] font-semibold whitespace-nowrap"
              >
                {t.add_source}
              </button>
            </div>

            {all.length === 0 ? (
              <div className="border-od-stroke bg-od-panel-deep-3 mt-[18px] rounded-[10px] border border-dashed p-[38px_28px]">
                <h3 className="text-od-text m-0 text-[18px] font-semibold">{t.empty_title}</h3>
                <p className="text-od-muted-4 mt-[10px] max-w-[58ch] text-pretty">{t.empty_body}</p>
                <button
                  type="button"
                  onClick={() =>
                    setDraft({ id: null, title: "", content: "", assistant_id: null })
                  }
                  className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 mt-4 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13.5px] font-semibold"
                >
                  {t.empty_action}
                </button>
              </div>
            ) : (
              <>
                <div className="mt-[18px] flex flex-wrap items-center gap-[10px]">
                  <div className="border-od-line bg-od-panel-deep-3 flex min-w-[240px] flex-[1_1_300px] items-center gap-[10px] rounded-lg border p-[9px_13px]">
                    <span className="text-od-faint-2 text-[15px]">⌕</span>
                    <input
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder={t.search_placeholder}
                      className="text-od-text-3 min-w-0 flex-1 border-none bg-transparent text-[14.5px] outline-none"
                    />
                  </div>
                </div>

                <p className="text-od-faint-2 mt-[14px] max-w-[70ch] text-pretty text-[12.5px]">
                  {t.index_pending}
                </p>

                <div className="border-od-line bg-od-panel-deep-3 mt-[10px] overflow-hidden rounded-[10px] border">
                  <div
                    className="border-od-line bg-od-canvas-2 text-od-faint-2 grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
                    style={{ gridTemplateColumns: COLUMNS }}
                  >
                    <span>{t.column_source}</span>
                    <span>{t.column_who}</span>
                    <span>{t.column_updated}</span>
                    <span />
                  </div>

                  {rows.map((source, index) => (
                    <div
                      key={source.id}
                      className={`grid items-center gap-[18px] p-[13px_18px] ${
                        index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
                      }`}
                      style={{ gridTemplateColumns: COLUMNS }}
                    >
                      <button
                        type="button"
                        onClick={() =>
                          setDraft({
                            id: source.id,
                            title: source.title,
                            content: source.content,
                            assistant_id: source.assistant_id,
                          })
                        }
                        className="min-w-0 cursor-pointer border-none bg-transparent p-0 text-start"
                      >
                        <div className="text-od-text font-medium text-pretty">{source.title}</div>
                        <div className="text-od-muted-4 mt-[2px] line-clamp-1 text-[12.5px]">
                          {source.content}
                        </div>
                      </button>

                      <span className="text-od-muted-4 text-[12.5px]">
                        {source.assistant_name ?? t.all_assistants}
                      </span>

                      <span className="text-od-faint-2 text-[12.5px]">
                        {edited(source.updated_at, locale)}
                      </span>

                      {confirming === source.id ? (
                        <span className="flex flex-wrap items-center justify-end gap-[8px]">
                          <span className="text-od-muted-4 max-w-[34ch] text-[12.5px] text-pretty">
                            {t.delete_confirm}
                          </span>
                          <button
                            type="button"
                            onClick={() => setConfirming(null)}
                            className="border-od-line text-od-muted-4 cursor-pointer rounded-md border bg-transparent p-[5px_10px] text-[12.5px]"
                          >
                            {t.cancel}
                          </button>
                          <button
                            type="button"
                            onClick={() => remove(source.id)}
                            disabled={busy}
                            className="border-od-red-border bg-od-red-bg cursor-pointer rounded-md border p-[5px_10px] text-[12.5px] font-medium text-[color:var(--od-red-text)] disabled:opacity-50"
                          >
                            {t.delete_source}
                          </button>
                        </span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirming(source.id)}
                          className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer justify-self-end rounded-md border bg-transparent p-[5px_10px] text-[12.5px]"
                        >
                          {t.delete_source}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : null}
      </div>

      {draft !== null ? (
        <SourceDialog
          t={t}
          draft={draft}
          assistants={assistants.data ?? []}
          onChange={setDraft}
          onClose={() => setDraft(null)}
          onSaved={() => {
            setDraft(null);
            list.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function SourceDialog({
  t,
  draft,
  assistants,
  onChange,
  onClose,
  onSaved,
}: {
  t: KnowledgeDictionary;
  draft: Draft;
  assistants: Assistant[];
  onChange: (draft: Draft) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "invalid_title") return t.error_title_missing;
      return thrown.message;
    }
    return String(thrown);
  };

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      if (draft.id === null) {
        await addKnowledge({
          title: draft.title,
          content: draft.content,
          assistant_id: draft.assistant_id,
        });
      } else {
        await changeKnowledge(draft.id, {
          title: draft.title,
          content: draft.content,
          assistant_id: draft.assistant_id,
        });
      }
      onSaved();
    } catch (thrown) {
      setProblem(describe(thrown));
      setBusy(false);
    }
  };

  const tooLong = draft.content.length > KNOWLEDGE_CONTENT_MAX;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-auto p-[60px_24px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div className="border-od-line bg-od-panel-deep-3 w-full max-w-[680px] rounded-xl border p-6">
        <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-3">
          <h2 className="text-od-text m-0 text-[19px] font-semibold">
            {draft.id === null ? t.add_source : t.edit_source}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] text-[13.5px]"
          >
            {t.close}
          </button>
        </div>

        <label className="mt-[18px] block">
          <span className="text-od-text-3 font-medium">{t.dialog_title}</span>
          <input
            value={draft.title}
            onChange={(event) => onChange({ ...draft, title: event.target.value })}
            className="border-od-line bg-od-canvas-2 text-od-text-3 mt-2 w-full rounded-lg border p-[9px_13px] text-[14.5px] outline-none"
          />
          <span className="text-od-faint-2 mt-[6px] block text-[12.5px]">
            {t.dialog_title_note}
          </span>
        </label>

        <label className="mt-4 block">
          <span className="text-od-text-3 font-medium">{t.dialog_text}</span>
          {/* Written by the customer, in the language their customers speak. */}
          <textarea
            dir="ltr"
            value={draft.content}
            onChange={(event) => onChange({ ...draft, content: event.target.value })}
            className="border-od-line bg-od-canvas-2 text-od-text-3 mt-2 min-h-[220px] w-full resize-y rounded-lg border p-[13px_14px] text-start text-[13.5px] leading-[1.7] outline-none"
          />
          <span className="mt-[6px] flex flex-wrap items-baseline justify-between gap-x-4">
            <span className="text-od-faint-2 max-w-[54ch] text-pretty text-[12.5px]">
              {t.dialog_text_note}
            </span>
            <span
              dir="ltr"
              className="mono ltr-data text-[12px]"
              style={{ color: tooLong ? "var(--od-red-text)" : "var(--od-faint-2)" }}
            >
              {draft.content.length} / {KNOWLEDGE_CONTENT_MAX}
            </span>
          </span>
        </label>

        <label className="mt-4 block max-w-[320px]">
          <span className="text-od-text-3 font-medium">{t.dialog_who}</span>
          <select
            value={draft.assistant_id ?? ""}
            onChange={(event) =>
              onChange({
                ...draft,
                assistant_id: event.target.value ? Number(event.target.value) : null,
              })
            }
            className="border-od-line bg-od-canvas-2 text-od-text-3 mt-2 w-full rounded-lg border p-[9px_13px] text-[14.5px] outline-none"
          >
            <option value="">{t.all_assistants}</option>
            {assistants.map((assistant) => (
              <option key={assistant.id} value={assistant.id}>
                {assistant.name}
              </option>
            ))}
          </select>
          {assistants.length === 0 ? (
            <span className="text-od-faint-2 mt-[6px] block text-pretty text-[12.5px]">
              {t.no_assistants_yet}
            </span>
          ) : null}
        </label>

        {problem !== null ? (
          <p className="mt-4 text-pretty text-[13px] text-[color:var(--od-red-text)]">{problem}</p>
        ) : null}

        <div className="mt-6 flex flex-wrap justify-end gap-[10px]">
          <button
            type="button"
            onClick={onClose}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] text-[13.5px]"
          >
            {t.cancel}
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={busy || draft.title.trim() === "" || draft.content === "" || tooLong}
            className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? t.saving : t.save}
          </button>
        </div>
      </div>
    </div>
  );
}
