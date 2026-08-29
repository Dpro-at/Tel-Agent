"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  conversationList,
  THREADS_PAGE,
  type Thread,
  type ThreadHandling,
  type ThreadPage,
} from "@/lib/api";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { CallsDictionary } from "./page";

type Key = keyof CallsDictionary;

const BADGES: Record<
  ThreadHandling,
  { label: Key; color: string; background: string; border: string }
> = {
  ai: {
    label: "handled_agent",
    color: "var(--od-violet-3)",
    background: "rgba(139,124,255,.13)",
    border: "var(--od-violet-border)",
  },
  human: {
    label: "handled_human",
    color: "var(--od-green-text)",
    background: "rgba(63,185,132,.11)",
    border: "var(--od-green-border)",
  },
  blocked: {
    label: "handled_blocked",
    color: "var(--od-red-text-4)",
    background: "rgba(240,96,94,.11)",
    border: "var(--od-red-border)",
  },
};

/** Nobody has handled it yet - that is a state, not a missing badge. */
const OPEN_BADGE = {
  color: "var(--od-amber-text)",
  background: "var(--od-amber-bg)",
  border: "var(--od-amber-border)",
};

/** Today and yesterday get their words; any other day is the date. */
function dayLabel(t: CallsDictionary, locale: Locale, iso: string): string {
  const started = new Date(iso);
  const day = started.toDateString();
  const now = new Date();
  if (day === now.toDateString()) return t.day_today;
  if (day === new Date(now.getTime() - 24 * 60 * 60 * 1000).toDateString())
    return t.day_yesterday;
  return started.toLocaleDateString(locale, { day: "numeric", month: "long" });
}

function clock(locale: Locale, iso: string): string {
  return new Date(iso).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
}

/** Wall-clock length of the call, as m:ss. `billable_seconds` lives on the detail;
 *  what the list can say honestly is when it started and when it ended. */
function length(row: Thread): string | null {
  if (row.ended_at === null) return null;
  const seconds = Math.max(
    0,
    Math.round((new Date(row.ended_at).getTime() - new Date(row.started_at).getTime()) / 1000),
  );
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** Five columns, shared by the header and every row so they cannot drift apart. */
const COLUMNS =
  "minmax(0,1fr) minmax(0,1.6fr) minmax(0,1fr) minmax(112px, max-content) minmax(72px, max-content)";

export function CallsList({ locale, t }: { locale: Locale; t: CallsDictionary }) {
  const router = useRouter();
  const [searchText, setSearchText] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);

  // The archive endpoint, narrowed to the phone channel. Search and paging happen
  // on the server; a browser-side filter over a capped page would hide older calls.
  const page = useResource<ThreadPage>(
    () => conversationList({ channel: "phone", q: query || undefined, offset }),
    [query, offset],
  );
  const rows = page.data?.threads ?? [];

  const search = (value: string) => {
    setOffset(0);
    setQuery(value.trim());
  };

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="calls" />
      </div>

      <div className="mx-auto max-w-[1400px] p-[26px_28px_80px]">
        <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
          <div>
            <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
              {t.title}
            </h1>
            <div className="text-od-muted-4 mt-[5px]">{t.subtitle}</div>
          </div>
        </div>

        {/* §A6.3: full-text search is the headline feature, so it gets the width. */}
        <form
          onSubmit={(event) => {
            event.preventDefault();
            search(searchText);
          }}
          className="mt-[18px] flex flex-wrap items-center gap-[10px]"
        >
          <div className="border-od-border-6 bg-od-panel-deep-3 flex min-w-[260px] flex-[1_1_380px] items-center gap-[10px] rounded-lg border p-[10px_14px]">
            <span className="text-od-faint text-[15px]">⌕</span>
            <input
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder={t.search_placeholder}
              aria-label={t.search_placeholder}
              maxLength={200}
              className="text-od-text-2 min-w-0 flex-1 border-none bg-transparent text-[15px] outline-none"
            />
          </div>
          <button
            type="submit"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-lg border p-[10px_15px] font-medium"
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
              className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-lg border bg-transparent p-[10px_15px]"
            >
              {t.search_clear}
            </button>
          ) : null}
        </form>

        {page.loading && page.data === null ? <ListSkeleton /> : null}

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
          <div className="border-od-border-6 bg-od-panel-deep-2 mt-5 rounded-[10px] border border-dashed p-[46px_30px] text-center">
            <h3 className="m-0 text-[19px] font-semibold">
              {query ? t.no_results_title : t.empty_title}
            </h3>
            <p className="text-od-muted mx-auto mt-[10px] max-w-[52ch] text-pretty">
              {query ? t.no_results_body : t.empty_body}
            </p>
          </div>
        ) : null}

        {rows.length > 0 ? (
          <div className="border-od-line bg-od-panel-deep-3 mt-5 overflow-x-auto overflow-y-hidden rounded-[10px] border">
            <div
              className="border-od-line bg-od-canvas-2 text-od-faint grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
              style={{ gridTemplateColumns: COLUMNS }}
            >
              <span>{t.column_when}</span>
              <span>{t.column_caller}</span>
              <span>{t.column_intent}</span>
              <span>{t.column_handled}</span>
              <span>{t.column_length}</span>
            </div>

            {rows.map((row) => {
              const badge = row.handling ? BADGES[row.handling] : null;
              const rowLength = length(row);
              return (
                <div
                  key={row.id}
                  onClick={() => router.push(`/${locale}/calls/${row.id}`)}
                  className="hover:bg-od-raise grid cursor-pointer items-start gap-[18px] border-b border-[color:var(--od-raise-6)] p-[14px_18px]"
                  style={{ gridTemplateColumns: COLUMNS }}
                >
                  <div>
                    <div className="text-od-text-3">{dayLabel(t, locale, row.started_at)}</div>
                    <div dir="ltr" className="mono ltr-data text-od-faint mt-[2px] text-[12.5px]">
                      {clock(locale, row.started_at)}
                    </div>
                  </div>
                  <div className="min-w-0">
                    {/* The phonebook's name when it has one; the number stays visible
                        underneath, because the name is an annotation on the record,
                        not a replacement for it. */}
                    {row.who_name ? (
                      <>
                        <div className="text-od-text font-medium text-pretty">
                          {row.who_name}
                        </div>
                        <div
                          dir="ltr"
                          className="mono ltr-data text-od-muted-5 mt-[2px] text-start text-[12.5px]"
                        >
                          {row.who}
                        </div>
                      </>
                    ) : row.who ? (
                      <div dir="ltr" className="mono ltr-data text-od-text text-start font-medium">
                        {row.who}
                      </div>
                    ) : (
                      <div className="text-od-text font-medium">{t.caller_unknown}</div>
                    )}
                    {row.preview ? (
                      <div
                        dir="ltr"
                        className="text-od-muted-5 mt-[3px] line-clamp-1 text-start text-[12.5px] text-pretty"
                      >
                        {row.preview}
                      </div>
                    ) : null}
                  </div>
                  <div className="text-pretty text-[color:var(--od-text-5)]">
                    {row.intent ?? "—"}
                  </div>
                  <div>
                    <span
                      className="inline-flex items-center gap-[7px] rounded-md border p-[3px_10px] text-[12.5px] font-medium whitespace-nowrap"
                      style={
                        badge
                          ? {
                              borderColor: badge.border,
                              background: badge.background,
                              color: badge.color,
                            }
                          : row.status === "open"
                            ? {
                                borderColor: OPEN_BADGE.border,
                                background: OPEN_BADGE.background,
                                color: OPEN_BADGE.color,
                              }
                            : {
                                borderColor: "var(--od-border-9)",
                                background: "var(--od-raise-5)",
                                color: "var(--od-muted-2)",
                              }
                      }
                    >
                      {badge ? t[badge.label] : row.status === "open" ? t.status_open : t.status_closed}
                    </span>
                  </div>
                  <div dir="ltr" className="mono ltr-data text-od-muted">
                    {rowLength ?? "—"}
                  </div>
                </div>
              );
            })}

            {offset > 0 || page.data?.has_more ? (
              <div className="flex items-center justify-between gap-[10px] p-[10px_18px]">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - THREADS_PAGE))}
                  className="border-od-border-7 text-od-muted-4 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] disabled:cursor-default disabled:opacity-40"
                >
                  {t.page_newer}
                </button>
                <button
                  type="button"
                  disabled={!page.data?.has_more}
                  onClick={() => setOffset(offset + THREADS_PAGE)}
                  className="border-od-border-7 text-od-muted-4 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] disabled:cursor-default disabled:opacity-40"
                >
                  {t.page_older}
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ListSkeleton() {
  const shimmer = (from: string, to: string) => ({
    background: `linear-gradient(90deg,var(${from}),var(${to}),var(${from}))`,
    backgroundSize: "420px 100%",
    animation: "od-shimmer 1.4s linear infinite",
  });

  return (
    <div className="border-od-line bg-od-panel-deep-3 mt-5 overflow-x-auto overflow-y-hidden rounded-[10px] border">
      {[72, 88, 64, 92, 78, 70, 86, 60].map((width, index) => (
        <div
          key={index}
          className="grid gap-[18px] border-b border-[color:var(--od-raise-6)] p-[16px_18px]"
          style={{ gridTemplateColumns: "150px minmax(0,1.6fr) 1fr 130px 90px" }}
        >
          <div className="h-3 rounded bg-[var(--od-raise-4)]" />
          <div
            className="h-3 rounded"
            style={{ width: `${width}%`, ...shimmer("--od-raise-2", "--od-raise-11") }}
          />
          <div className="h-3 rounded bg-[var(--od-raise-4)]" />
          <div className="h-5 rounded-[5px] bg-[var(--od-raise-8)]" />
          <div className="h-3 rounded bg-[var(--od-raise-4)]" />
        </div>
      ))}
    </div>
  );
}
