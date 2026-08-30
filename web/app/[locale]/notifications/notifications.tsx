"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  markLogRead,
  notificationList,
  resolveNotification,
  type NotificationCategory,
  type NotificationItem,
  type NotificationList,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { NotificationsDictionary } from "./page";

type Key = keyof NotificationsDictionary;
type Filter = "all" | NotificationCategory;

/**
 * The server's four categories, and the words the screen already had for them.
 *
 * The server says `failure`; the filter chip says "Failures". Mapping here rather than
 * renaming either side keeps the API's vocabulary singular — one row, one failure —
 * without making the chip read oddly.
 */
const FILTERS: { id: Filter; label: Key }[] = [
  { id: "all", label: "filter_all" },
  { id: "failure", label: "filter_failures" },
  { id: "review", label: "filter_review" },
  { id: "missed", label: "filter_missed" },
  { id: "system", label: "filter_system" },
];

const CATEGORY_TAG: Record<NotificationCategory, Key> = {
  failure: "tag_tool_failed",
  review: "tag_review",
  missed: "tag_missed",
  system: "tag_system",
};

const SEVERITY = {
  red: {
    border: "var(--od-red-border-3)",
    background: "var(--od-red-bg-4)",
    dot: "#F0605E",
    tagColor: "var(--od-red-text-5)",
    tagBorder: "var(--od-red-border-3)",
    tagBackground: "var(--od-red-bg-5)",
    title: "var(--od-red-text-3)",
    body: "var(--od-red-text-6)",
    primaryBorder: "var(--od-red-border-2)",
    primaryBackground: "var(--od-red-bg-2)",
    primaryColor: "var(--od-red-text-3)",
  },
  amber: {
    border: "var(--od-amber-border-2)",
    background: "var(--od-amber-bg-2)",
    dot: "var(--od-amber)",
    tagColor: "var(--od-amber-text)",
    tagBorder: "var(--od-amber-border)",
    tagBackground: "var(--od-amber-bg)",
    title: "var(--od-amber-text-2)",
    body: "var(--od-amber-text-3)",
    primaryBorder: "var(--od-amber-border)",
    primaryBackground: "var(--od-amber-bg)",
    primaryColor: "var(--od-amber-text-2)",
  },
} as const;

/**
 * The sentence for one notification, in the reader's language.
 *
 * The server sends a key and its parameters; the sentences live here, in three
 * languages. A key with no sentence renders as the key itself rather than as an empty
 * row — a screen that silently drops a notification is worse than one that shows an
 * identifier somebody can search for, and the test suite is what stops that from
 * happening in the first place.
 */
function sentence(t: NotificationsDictionary, item: NotificationItem): string {
  const line = (t as Record<string, string>)[`msg_${item.message_key}`];
  if (line === undefined) return item.message_key;
  return interpolate(line, item.params);
}

/** A failure is red; anything else waiting on a person is amber. */
function severityOf(item: NotificationItem): "red" | "amber" {
  return item.category === "failure" ? "red" : "amber";
}

/** The clock time, in UTC as the server sent it. */
function clock(iso: string): string {
  return iso.slice(11, 16);
}

export function Notifications({ locale, t }: { locale: Locale; t: NotificationsDictionary }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [busy, setBusy] = useState<number | "log" | null>(null);
  const [notice, setNotice] = useState<{ text: string; bad?: boolean } | null>(null);

  // Filtering happens on the server: the list is capped at a limit, so filtering the
  // page in the browser would quietly hide older items of the chosen category.
  const list = useResource<NotificationList>(
    () => notificationList(filter === "all" ? undefined : filter),
    [filter],
  );

  if (list.loading && list.data === null) {
    return (
      <Frame locale={locale}>
        <Head t={t} />
        <div className="mt-[18px] flex flex-col gap-[10px]">
          {[0, 1, 2, 3, 4].map((index) => (
            <div
              key={index}
              className="border-od-raise-12 h-[86px] rounded-[10px] border"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
          ))}
        </div>
      </Frame>
    );
  }

  if (list.error !== null && list.data === null) {
    return (
      <Frame locale={locale}>
        <Head t={t} />
        <div className="border-od-red-border-3 bg-od-red-bg-4 mt-5 rounded-[10px] border p-[18px_20px]">
          <h3 className="m-0 text-[16px] font-semibold text-[color:var(--od-red-text-3)]">
            {list.error.kind === "offline" ? t.error_offline_title : t.error_failed_title}
          </h3>
          <p className="mt-[6px] max-w-[62ch] text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
            {list.error.message}
          </p>
          <button
            type="button"
            onClick={list.reload}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
          >
            {t.retry}
          </button>
        </div>
      </Frame>
    );
  }

  const data = list.data as NotificationList;
  const nothingAtAll = data.waiting.length === 0 && data.log.length === 0;

  async function act(which: number | "log", work: () => Promise<string | null>) {
    setBusy(which);
    setNotice(null);
    try {
      const message = await work();
      if (message) setNotice({ text: message });
      list.reload();
    } catch (thrown) {
      setNotice({ text: thrown instanceof Error ? thrown.message : String(thrown), bad: true });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Frame locale={locale}>
      <Head
        t={t}
        action={{
          label: busy === "log" ? t.resolving : t.mark_all_read,
          disabled: busy !== null || data.log.length === 0,
          onClick: () =>
            act("log", async () => {
              const result = await markLogRead();
              return result.resolved === 0
                ? t.nothing_to_mark
                : interpolate(t.marked_read, {
                    count: String(result.resolved),
                    waiting: String(result.still_waiting),
                  });
            }),
        }}
      />

      <div className="mt-5 flex flex-wrap gap-2">
        {FILTERS.map((entry) => {
          const on = filter === entry.id;
          return (
            <button
              key={entry.id}
              type="button"
              onClick={() => setFilter(entry.id)}
              className={`inline-flex cursor-pointer items-center gap-2 rounded-full border p-[7px_13px] text-[13px] whitespace-nowrap ${
                on
                  ? "border-od-stroke bg-od-raise-10 text-od-text"
                  : "border-od-border-7 text-od-muted-4 bg-transparent"
              }`}
            >
              <span>{t[entry.label]}</span>
              {/* A count is shown only for the chip in force. The others would need
                  their own request, and a stale number beside a chip is worse than
                  no number at all. */}
              {on ? (
                <span dir="ltr" className="mono ltr-data text-od-muted-4 text-[11.5px]">
                  {data.waiting.length + data.log.length}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {notice ? (
        <div
          className="mt-4 rounded-[10px] border p-[12px_15px] text-[13px] text-pretty"
          style={{
            borderColor: notice.bad ? "var(--od-red-border-3)" : "var(--od-border-7)",
            background: notice.bad ? "var(--od-red-bg-4)" : "var(--od-panel-deep-2)",
            color: notice.bad ? "var(--od-red-text-6)" : "var(--od-muted)",
          }}
        >
          {notice.text}
        </div>
      ) : null}

      {nothingAtAll ? (
        <div className="border-od-border-6 bg-od-panel-deep-2 mt-5 rounded-[10px] border border-dashed p-[34px_28px]">
          <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
          <p className="text-od-muted mt-[9px] max-w-[58ch] text-pretty">{t.empty_body}</p>
        </div>
      ) : null}

      {data.waiting.length > 0 ? (
        <section className="mt-[22px]">
          <div className="flex flex-wrap items-baseline justify-between gap-x-[14px] gap-y-2">
            <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
              {t.open_heading}
            </h2>
            <span className="text-od-faint text-[12.5px]">
              {data.waiting.length === 1
                ? t.items_one
                : interpolate(t.items_many, { count: data.waiting.length })}
            </span>
          </div>
          <div className="mt-3 flex flex-col gap-[10px]">
            {data.waiting.map((item) => {
              const tone = SEVERITY[severityOf(item)];
              return (
                <div
                  key={item.id}
                  className="rounded-[10px] border p-[13px_16px]"
                  style={{ borderColor: tone.border, background: tone.background }}
                >
                  <div className="flex flex-wrap items-start gap-x-[18px] gap-y-3">
                    <span
                      className="mt-[6px] size-[9px] flex-none rounded-full"
                      style={{ background: tone.dot }}
                    />
                    <div className="min-w-[240px] flex-[1_1_320px]">
                      <div className="flex flex-wrap items-center gap-[10px]">
                        <span
                          className="rounded-[5px] border p-[2px_8px] text-[11.5px] font-bold tracking-[.05em] uppercase whitespace-nowrap"
                          style={{
                            borderColor: tone.tagBorder,
                            background: tone.tagBackground,
                            color: tone.tagColor,
                          }}
                        >
                          {t[CATEGORY_TAG[item.category]]}
                        </span>
                        <span dir="ltr" className="mono ltr-data text-od-faint text-[12px]">
                          {clock(item.created_at)}
                        </span>
                      </div>
                      <div
                        className="mt-[6px] text-[15px] font-semibold text-pretty"
                        style={{ color: tone.title }}
                      >
                        {sentence(t, item)}
                      </div>
                      {/* The machine's words, shown as machine output: monospace, left
                          to right, and visibly not part of the translated sentence
                          above it. A provider's error names the host and port, which is
                          the part somebody can act on - and inventing a translated
                          sentence for every error a provider can return is not
                          possible. */}
                      {item.detail ? (
                        <div
                          dir="ltr"
                          className="mono ltr-data mt-[7px] max-w-[80ch] text-[11.5px] [overflow-wrap:anywhere]"
                          style={{ color: tone.body }}
                        >
                          {item.detail}
                        </div>
                      ) : null}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {/* One button, not two. The second was a fixture: `primary_action`
                          names a repair — resend the SMS, retry the webhook — and none
                          of those have an endpoint yet. Drawing them would be a button
                          that does nothing. */}
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() =>
                          act(item.id, async () => {
                            await resolveNotification(item.id);
                            return null;
                          })
                        }
                        className="cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium whitespace-nowrap disabled:opacity-50"
                        style={{
                          borderColor: tone.primaryBorder,
                          background: tone.primaryBackground,
                          color: tone.primaryColor,
                        }}
                      >
                        {busy === item.id ? t.resolving : t.resolve}
                      </button>
                      {/* "Open conversation", not "Open call": a call is one kind of
                          conversation and the web chat is another, and this button is
                          drawn from `conversation_id`, which every channel has. The
                          label it used to borrow belonged to a fixture about an SMS. */}
                      {item.conversation_id ? (
                        <Link
                          // The thread itself, not the list with it somewhere in it.
                          // A tray exists to be emptied, and a button that lands
                          // somebody on a list to search for what they just clicked is
                          // a button that costs more than it saves.
                          href={`/${locale}/conversations?thread=${item.conversation_id}`}
                          className="cursor-pointer rounded-md border bg-transparent p-[8px_14px] text-[13px] font-medium whitespace-nowrap"
                          style={{ borderColor: tone.primaryBorder, color: tone.primaryColor }}
                        >
                          {t.open_conversation}
                        </Link>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      ) : null}

      {data.log.length > 0 ? (
        <section className="mt-[26px]">
          <h2 className="text-od-muted-4 mt-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.log_heading}
          </h2>
          <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
            {data.log.map((item, index) => {
              // Resolved is the ✓; a failure nobody marked as dealt with is the "!".
              const settled = item.resolved_at !== null || item.category !== "failure";
              return (
                <div
                  key={item.id}
                  className={`flex flex-wrap items-start gap-x-[14px] gap-y-[10px] p-[13px_16px] ${
                    index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
                  }`}
                >
                  <span
                    className="inline-flex size-[21px] flex-none items-center justify-center rounded-full border text-[11.5px] leading-none font-bold"
                    style={{
                      borderColor: settled ? "var(--od-green-border)" : "var(--od-amber-border)",
                      background: settled ? "rgba(63,185,132,.11)" : "var(--od-amber-bg)",
                      color: settled ? "var(--od-green-text)" : "var(--od-amber-text)",
                    }}
                  >
                    {settled ? "✓" : "!"}
                  </span>
                  <div className="min-w-[240px] flex-[1_1_300px]">
                    <div className="text-od-text-3 text-pretty">{sentence(t, item)}</div>
                    {item.detail ? (
                      <div
                        dir="ltr"
                        className="mono ltr-data text-od-muted-5 mt-[4px] max-w-[80ch] text-[11.5px] [overflow-wrap:anywhere]"
                      >
                        {item.detail}
                      </div>
                    ) : null}
                  </div>
                  <span
                    dir="ltr"
                    className="mono ltr-data text-od-faint-2 flex-none text-[12px] whitespace-nowrap"
                  >
                    {item.created_at.slice(0, 16).replace("T", " ")}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="border-od-line bg-od-panel-deep-2 mt-[14px] flex flex-wrap items-center justify-between gap-x-[18px] gap-y-[10px] rounded-[9px] border p-[13px_15px]">
            <span className="text-od-muted max-w-[62ch] text-[12.5px] text-pretty">
              {t.retention_note}
            </span>
            <Link
              href={`/${locale}/settings`}
              className="text-od-violet text-[13px] whitespace-nowrap hover:underline"
            >
              {t.email_settings}
            </Link>
          </div>
        </section>
      ) : null}
    </Frame>
  );
}

function Head({
  t,
  action,
}: {
  t: NotificationsDictionary;
  action?: { label: string; disabled: boolean; onClick: () => void };
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-5 gap-y-[14px]">
      <div className="min-w-0 max-w-[64ch] flex-[1_1_320px]">
        <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
          {t.title}
        </h1>
        <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
      </div>
      {action ? (
        <button
          type="button"
          disabled={action.disabled}
          onClick={action.onClick}
          className="border-od-border-2 text-od-muted hover:text-od-text-2 flex-none cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] text-[13px] whitespace-nowrap hover:bg-[var(--od-raise-4)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {action.label}
        </button>
      ) : null}
    </div>
  );
}

function Frame({ locale, children }: { locale: Locale; children: React.ReactNode }) {
  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="notifications" />
      </div>
      <div className="mx-auto max-w-[1000px] p-[26px_28px_80px]">{children}</div>
    </div>
  );
}
