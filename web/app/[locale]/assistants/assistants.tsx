"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  addAssistant,
  ApiError,
  assistantsList,
  type Assistant,
  type AssistantTemplate,
} from "@/lib/api";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { AssistantsDictionary } from "./page";

/**
 * The design drew four columns: name, the channels it answers on, the number it is
 * connected to, and when it was last active. Three of those four are channel data,
 * and channels are the last thing built - so they are not drawn here yet rather than
 * drawn empty. A column that can only ever show a dash teaches the reader that the
 * table is decorative, and they stop reading the columns that are not.
 *
 * They come back with the channels, against real rows, and the note under the heading
 * says so in the meantime.
 */
const COLUMNS =
  "minmax(0,1.8fr) minmax(90px,max-content) minmax(110px,max-content) minmax(110px,max-content)";

const TEMPLATE_KEYS: Record<AssistantTemplate, keyof AssistantsDictionary> = {
  reception: "template_reception",
  ooh: "template_ooh",
  overflow: "template_overflow",
  blank: "template_blank",
};

function StatusPill({ paused, label }: { paused: boolean; label: string }) {
  return (
    <span
      className="rounded-md border p-[3px_9px] text-[12px] font-medium whitespace-nowrap"
      style={{
        borderColor: paused ? "var(--od-border-7)" : "var(--od-green-border)",
        background: paused ? "var(--od-raise-5)" : "rgba(63,185,132,.11)",
        color: paused ? "var(--od-muted-4)" : "var(--od-green-text)",
      }}
    >
      {label}
    </span>
  );
}

function Skeleton() {
  const shimmer = {
    background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  };
  return (
    <div>
      <div className="h-7 w-[190px] rounded-md" style={shimmer} />
      <div className="mt-6 flex flex-col gap-[10px]">
        {[0, 1, 2, 3].map((index) => (
          <div
            key={index}
            className="border-od-line h-[62px] rounded-[10px] border"
            style={shimmer}
          />
        ))}
      </div>
    </div>
  );
}

/** The date as the reader's locale writes it, not as the server stores it. */
function edited(iso: string, locale: Locale): string {
  return new Date(iso).toLocaleDateString(locale === "ar" ? "ar" : locale, {
    day: "numeric",
    month: "short",
  });
}

export function Assistants({ locale, t }: { locale: Locale; t: AssistantsDictionary }) {
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);

  const list = useResource<Assistant[]>(() => assistantsList(), []);
  const all = list.data ?? [];

  // Filtered here rather than at the server: a workspace has a handful of assistants,
  // and a round trip per keystroke would be slower than the filter it replaces.
  const needle = search.trim().toLowerCase();
  const rows = needle
    ? all.filter(
        (row) =>
          row.name.toLowerCase().includes(needle) ||
          (row.role ?? "").toLowerCase().includes(needle),
      )
    : all;

  return (
    <div className="bg-od-canvas text-od-text-3 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="assistants" />
      </div>

      <div className="mx-auto max-w-[1240px] p-[22px_28px_70px]">
        {list.loading && list.data === null ? <Skeleton /> : null}

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
                onClick={() => setCreating(true)}
                className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13.5px] font-semibold whitespace-nowrap"
              >
                {t.create}
              </button>
            </div>

            {all.length > 0 ? (
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
            ) : null}

            {all.length === 0 ? (
              <div className="border-od-stroke bg-od-panel-deep-3 mt-[18px] rounded-[10px] border border-dashed p-[38px_28px]">
                <h3 className="text-od-text m-0 text-[18px] font-semibold">{t.empty_title}</h3>
                <p className="text-od-muted-4 mt-[10px] max-w-[58ch] text-pretty">{t.empty_body}</p>
                <div className="mt-4 flex flex-wrap gap-[10px]">
                  <button
                    type="button"
                    onClick={() => setCreating(true)}
                    className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13.5px] font-semibold"
                  >
                    {t.empty_create}
                  </button>
                  <Link
                    href={`/${locale}/install`}
                    className="border-od-line text-od-muted-4 hover:text-od-text-2 inline-block rounded-[7px] border p-[9px_15px] text-[13.5px] hover:no-underline"
                  >
                    {t.empty_setup}
                  </Link>
                </div>
              </div>
            ) : (
              <>
                <p className="text-od-faint-2 mt-[14px] max-w-[70ch] text-pretty text-[12.5px]">
                  {t.channels_pending}
                </p>
                <div className="border-od-line bg-od-panel-deep-3 mt-[10px] overflow-hidden rounded-[10px] border">
                  <div
                    className="border-od-line bg-od-canvas-2 text-od-faint-2 grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
                    style={{ gridTemplateColumns: COLUMNS }}
                  >
                    <span>{t.column_name}</span>
                    <span>{t.column_status}</span>
                    <span>{t.column_template}</span>
                    <span>{t.column_updated}</span>
                  </div>

                  {rows.map((assistant, index) => (
                    <Link
                      key={assistant.id}
                      href={`/${locale}/assistants/${assistant.id}`}
                      className={`hover:bg-od-raise grid cursor-pointer items-center gap-[18px] p-[13px_18px] text-inherit hover:no-underline ${
                        index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
                      }`}
                      style={{ gridTemplateColumns: COLUMNS }}
                    >
                      <div className="flex min-w-0 items-center gap-[11px]">
                        <span className="border-od-line text-od-text-3 inline-flex size-8 flex-none items-center justify-center rounded-full border bg-[var(--od-raise-5)] text-[13px] font-semibold">
                          {assistant.name.slice(0, 1)}
                        </span>
                        <div className="min-w-0">
                          <div className="text-od-text font-medium text-pretty">
                            {assistant.name}
                          </div>
                          <div
                            className="mt-[2px] text-[12.5px] text-pretty"
                            style={{
                              color: assistant.role
                                ? "var(--od-muted-4)"
                                : "var(--od-faint-2)",
                            }}
                          >
                            {assistant.role ?? t.no_role}
                          </div>
                        </div>
                      </div>

                      <StatusPill
                        paused={assistant.status === "paused"}
                        label={
                          assistant.status === "paused" ? t.status_paused : t.status_active
                        }
                      />

                      <span className="text-od-muted-4 text-[12.5px]">
                        {t[TEMPLATE_KEYS[assistant.template]]}
                      </span>

                      <span className="text-od-faint-2 text-[12.5px]">
                        {edited(assistant.updated_at, locale)}
                      </span>
                    </Link>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : null}
      </div>

      {creating ? (
        <CreateDialog
          t={t}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            list.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function CreateDialog({
  t,
  onClose,
  onCreated,
}: {
  t: AssistantsDictionary;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  /** The server's code, in the reader's language — its message is English. */
  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "name_taken") return t.error_name_taken;
      if (thrown.code === "invalid_name") return t.error_name_missing;
      return thrown.message;
    }
    return String(thrown);
  };

  const submit = async () => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await addAssistant({ name, role: role.trim() || null });
      onCreated();
    } catch (thrown) {
      setProblem(describe(thrown));
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-auto p-[60px_24px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div className="border-od-line bg-od-panel-deep-3 w-full max-w-[560px] rounded-xl border p-6">
        <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-3">
          <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.create}</h2>
          <button
            type="button"
            onClick={onClose}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] text-[13.5px]"
          >
            {t.close}
          </button>
        </div>

        <label className="mt-[18px] block">
          <span className="text-od-text-3 font-medium">{t.dialog_name}</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="border-od-line bg-od-canvas-2 text-od-text-3 mt-2 w-full rounded-lg border p-[9px_13px] text-[14.5px] outline-none"
          />
          <span className="text-od-faint-2 mt-[6px] block text-pretty text-[12.5px]">
            {t.dialog_name_note}
          </span>
        </label>

        <label className="mt-4 block">
          <span className="text-od-text-3 font-medium">{t.dialog_role}</span>
          <input
            value={role}
            onChange={(event) => setRole(event.target.value)}
            placeholder={t.dialog_role_placeholder}
            className="border-od-line bg-od-canvas-2 text-od-text-3 mt-2 w-full rounded-lg border p-[9px_13px] text-[14.5px] outline-none"
          />
          <span className="text-od-faint-2 mt-[6px] block text-pretty text-[12.5px]">
            {t.dialog_role_note}
          </span>
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
            disabled={busy || name.trim() === ""}
            className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy ? t.saving : t.create}
          </button>
        </div>
      </div>
    </div>
  );
}
