"use client";

import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  addContact,
  ApiError,
  changeContact,
  CONTACTS_PAGE,
  contactsList,
  removeContact,
  type ContactPage,
} from "@/lib/api";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { ContactsDictionary } from "./page";

const COLUMNS =
  "minmax(0,1.5fr) minmax(150px,1fr) minmax(0,1fr) minmax(120px,max-content) max-content";

function Tag({ label }: { label: string }) {
  return (
    <span className="border-od-border-7 text-od-muted-4 rounded-full border bg-[var(--od-raise-5)] p-[1px_8px] text-[11px] font-medium whitespace-nowrap">
      {label}
    </span>
  );
}

type Draft = { name: string; e164: string; tags: string; notes: string };

const EMPTY_DRAFT: Draft = { name: "", e164: "", tags: "", notes: "" };

/** The comma-separated field, as the list the server keeps. */
function tagsOf(draft: Draft): string[] {
  return draft.tags
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

export function Contacts({ locale, t }: { locale: Locale; t: ContactsDictionary }) {
  const [searchText, setSearchText] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const page = useResource<ContactPage>(
    () => contactsList({ q: query || undefined, offset }),
    [query, offset],
  );
  const rows = page.data?.contacts ?? [];

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "invalid_e164") return t.error_invalid_e164;
      if (thrown.code === "contact_exists") return t.error_contact_exists;
      return thrown.message;
    }
    return String(thrown);
  };

  const search = (value: string) => {
    setOffset(0);
    setQuery(value.trim());
  };

  const submit = async () => {
    if (draft === null) return;
    setBusy(true);
    setNotice(null);
    try {
      if (editing === null) {
        await addContact({
          e164: draft.e164.trim(),
          name: draft.name.trim(),
          tags: tagsOf(draft),
          notes: draft.notes.trim() || undefined,
        });
      } else {
        await changeContact(editing, {
          name: draft.name.trim(),
          tags: tagsOf(draft),
          notes: draft.notes.trim() || null,
        });
      }
      setDraft(null);
      setEditing(null);
      page.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    setBusy(true);
    setNotice(null);
    try {
      await removeContact(id);
      setConfirming(null);
      page.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="contacts" />
      </div>

      <div className="mx-auto max-w-[1400px] p-[22px_28px_60px]">
        <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
          <div className="max-w-[64ch]">
            <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
              {t.title}
            </h1>
            <p className="text-od-muted-4 mt-[6px] text-pretty">{t.subtitle}</p>
          </div>
          <button
            type="button"
            onClick={() => {
              setEditing(null);
              setDraft(draft === null ? EMPTY_DRAFT : null);
              setNotice(null);
            }}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 flex-none cursor-pointer rounded-[7px] border p-[9px_15px] font-medium"
          >
            {t.add_contact}
          </button>
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            search(searchText);
          }}
          className="mt-[16px] flex flex-wrap items-center gap-[10px]"
        >
          <input
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder={t.search_placeholder}
            aria-label={t.search_placeholder}
            maxLength={120}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 min-w-0 max-w-[400px] flex-[1_1_260px] rounded-lg border p-[9px_13px] outline-none"
          />
          <button
            type="submit"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-lg border p-[9px_14px] text-[13.5px] font-medium"
          >
            {t.search}
          </button>
          {query ? (
            <button
              type="button"
              onClick={() => {
                setSearchText("");
                search("");
              }}
              className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-lg border bg-transparent p-[9px_14px] text-[13.5px]"
            >
              {t.search_clear}
            </button>
          ) : null}
        </form>

        {draft !== null ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
            className="border-od-line bg-od-panel-deep-3 mt-[16px] rounded-[10px] border p-[16px_18px]"
          >
            <div className="flex flex-wrap items-end gap-[14px]">
              <label className="flex min-w-[180px] flex-[1_1_220px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_name}
                </span>
                <input
                  value={draft.name}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                  required
                  maxLength={120}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-lg border p-[9px_13px] outline-none"
                />
              </label>
              <label className="flex min-w-[180px] flex-[1_1_200px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_number}
                </span>
                <input
                  value={draft.e164}
                  onChange={(event) => setDraft({ ...draft, e164: event.target.value })}
                  placeholder="+43 664 123456"
                  dir="ltr"
                  required
                  maxLength={25}
                  disabled={editing !== null}
                  title={editing !== null ? t.field_number_locked : undefined}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono ltr-data rounded-lg border p-[9px_13px] text-start outline-none disabled:opacity-50"
                />
              </label>
              <label className="flex min-w-[160px] flex-[1_1_200px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_tags}
                </span>
                <input
                  value={draft.tags}
                  onChange={(event) => setDraft({ ...draft, tags: event.target.value })}
                  placeholder={t.field_tags_hint}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-lg border p-[9px_13px] outline-none"
                />
              </label>
              <label className="flex min-w-[200px] flex-[2_1_260px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_notes}
                </span>
                <input
                  value={draft.notes}
                  onChange={(event) => setDraft({ ...draft, notes: event.target.value })}
                  maxLength={2000}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-lg border p-[9px_13px] outline-none"
                />
              </label>
              <button
                type="submit"
                disabled={busy}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-lg border p-[9px_15px] font-medium disabled:cursor-default disabled:opacity-50"
              >
                {editing === null ? t.form_add : t.form_save}
              </button>
              <button
                type="button"
                onClick={() => {
                  setDraft(null);
                  setEditing(null);
                  setNotice(null);
                }}
                className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-lg border bg-transparent p-[9px_15px]"
              >
                {t.form_cancel}
              </button>
            </div>
          </form>
        ) : null}

        {notice ? (
          <div className="border-od-red-border-3 bg-od-red-bg-4 mt-[14px] rounded-[9px] border p-[10px_14px] text-[13px] text-[color:var(--od-red-text-6)]">
            {notice}
          </div>
        ) : null}

        {page.loading && page.data === null ? <ContactsSkeleton /> : null}

        {page.error !== null && page.data === null ? (
          <div className="border-od-red-border-3 bg-od-red-bg-4 mt-5 rounded-[10px] border p-[18px_20px]">
            <h3 className="m-0 text-[16px] font-semibold text-[color:var(--od-red-text-3)]">
              {page.error.kind === "offline" ? t.error_offline_title : t.error_failed_title}
            </h3>
            <p className="mt-[6px] max-w-[62ch] text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
              {page.error.message}
            </p>
            <button
              type="button"
              onClick={page.reload}
              className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
            >
              {t.retry}
            </button>
          </div>
        ) : null}

        {page.data !== null && rows.length === 0 ? (
          <div className="border-od-border-6 bg-od-panel-deep-2 mt-[18px] rounded-[10px] border border-dashed p-[40px_28px]">
            <h3 className="m-0 text-[18px] font-semibold">
              {query ? t.no_results_title : t.empty_title}
            </h3>
            <p className="text-od-muted mt-[10px] max-w-[62ch] text-pretty">
              {query ? t.no_results_body : t.empty_body}
            </p>
          </div>
        ) : null}

        {rows.length > 0 ? (
          <div className="border-od-line bg-od-panel-deep-3 mt-[18px] overflow-x-auto overflow-y-hidden rounded-[10px] border">
            <div
              className="border-od-line bg-od-canvas-2 text-od-faint grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
              style={{ gridTemplateColumns: COLUMNS }}
            >
              <span>{t.column_name}</span>
              <span>{t.column_number}</span>
              <span>{t.column_notes}</span>
              <span>{t.column_last}</span>
              <span aria-hidden="true" />
            </div>

            {rows.map((row) => (
              <div
                key={row.id}
                className="grid items-center gap-[18px] border-b border-[color:var(--od-raise-6)] p-[13px_18px]"
                style={{ gridTemplateColumns: COLUMNS }}
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-od-text font-medium text-pretty">{row.name}</span>
                    {row.tags.map((tag) => (
                      <Tag key={tag} label={tag} />
                    ))}
                  </div>
                </div>
                <div dir="ltr" className="mono ltr-data text-od-muted-5 text-start text-[12.5px]">
                  {row.e164}
                </div>
                <div className="text-od-muted-2 min-w-0 truncate text-[13px]">
                  {row.notes ?? ""}
                </div>
                <div className="text-od-faint text-[12.5px]">
                  {row.last_heard_at
                    ? new Date(row.last_heard_at).toLocaleDateString(locale, {
                        day: "numeric",
                        month: "short",
                      })
                    : t.never_heard}
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      setEditing(row.id);
                      setDraft({
                        name: row.name,
                        e164: row.e164,
                        tags: row.tags.join(", "),
                        notes: row.notes ?? "",
                      });
                      setNotice(null);
                    }}
                    className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] hover:bg-[var(--od-raise-4)] disabled:cursor-default disabled:opacity-50"
                  >
                    {t.action_edit}
                  </button>
                  {confirming === row.id ? (
                    <>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void remove(row.id)}
                        className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[6px_12px] text-[12.5px] font-medium text-[color:var(--od-red-text-3)] disabled:cursor-default disabled:opacity-50"
                      >
                        {t.remove_confirm}
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        className="border-od-border-2 text-od-muted cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px]"
                      >
                        {t.remove_cancel}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setConfirming(row.id)}
                      className="border-od-border-7 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] text-[color:var(--od-red-text-4)] hover:bg-[var(--od-raise-4)] disabled:cursor-default disabled:opacity-50"
                    >
                      {t.action_remove}
                    </button>
                  )}
                </div>
              </div>
            ))}

            {offset > 0 || page.data?.has_more ? (
              <div className="flex items-center justify-between gap-[10px] p-[10px_18px]">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - CONTACTS_PAGE))}
                  className="border-od-border-7 text-od-muted-4 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] disabled:cursor-default disabled:opacity-40"
                >
                  {t.page_previous}
                </button>
                <button
                  type="button"
                  disabled={!page.data?.has_more}
                  onClick={() => setOffset(offset + CONTACTS_PAGE)}
                  className="border-od-border-7 text-od-muted-4 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] disabled:cursor-default disabled:opacity-40"
                >
                  {t.page_next}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ContactsSkeleton() {
  return (
    <div className="mt-[18px] flex flex-col gap-3">
      {[0, 1, 2, 3, 4].map((index) => (
        <div
          key={index}
          className="border-od-raise-12 h-14 rounded-[10px] border"
          style={{
            background:
              "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
            backgroundSize: "420px 100%",
            animation: "od-shimmer 1.4s linear infinite",
          }}
        />
      ))}
    </div>
  );
}
