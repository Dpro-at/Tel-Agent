"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import { ChannelMark } from "@/components/shell/channel-mark";
import {
  conversationChannels,
  conversationDetail,
  conversationList,
  THREADS_PAGE,
  type ConversationChannel,
  type Thread,
  type ThreadDetail,
  type ThreadMessage,
  type ThreadPage,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { ConversationsDictionary } from "./page";

type Key = keyof ConversationsDictionary;

/**
 * Channel names are product names and are never translated - WhatsApp is WhatsApp
 * in every language. The kinds the core ships marks for are in `ChannelMark`; a
 * kind this map does not name still renders, in the quiet default colours.
 */
const CHANNEL_STYLE: Record<string, { color: string; border: string; background: string }> = {
  whatsapp: {
    color: "var(--od-green-text)",
    border: "var(--od-green-border)",
    background: "rgba(63,185,132,.11)",
  },
};

const DEFAULT_STYLE = {
  color: "var(--od-muted-2)",
  border: "var(--od-border-9)",
  background: "var(--od-raise-5)",
};

/** The product's own names for the channels that are not a company. */
const CHANNEL_LABEL_KEYS: Record<string, Key> = {
  web: "channel_web",
  phone: "channel_phone",
  sms: "channel_sms",
  email: "channel_email",
};

/** WhatsApp is WhatsApp in every language; `web` has a key. Anything else is shown
 *  capitalised as the server said it, so a community channel still reads as a name. */
function channelLabel(t: ConversationsDictionary, kind: string): string {
  const key = CHANNEL_LABEL_KEYS[kind];
  if (key) return t[key];
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** What one row's state line says, from the two columns that actually exist. */
function stateOf(thread: Thread): { label: Key; settled: boolean } {
  if (thread.handling === "ai") return { label: "handling_ai", settled: true };
  if (thread.handling === "human") return { label: "handling_human", settled: true };
  if (thread.handling === "blocked") return { label: "handling_blocked", settled: true };
  if (thread.status === "closed") return { label: "status_closed", settled: true };
  // Open and nobody has handled it - the one state that asks for attention.
  return { label: "status_open", settled: false };
}

/** Today and yesterday get their words; any other day is the date, in the reader's
 *  locale. Grouping compares local calendar days, because "today" is the reader's. */
function dayLabel(t: ConversationsDictionary, locale: Locale, iso: string): string {
  const started = new Date(iso);
  const day = started.toDateString();
  const now = new Date();
  if (day === now.toDateString()) return t.day_today;
  if (day === new Date(now.getTime() - 24 * 60 * 60 * 1000).toDateString())
    return t.day_yesterday;
  return started.toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "long" });
}

function clock(locale: Locale, iso: string, plusMs = 0): string {
  return new Date(new Date(iso).getTime() + plusMs).toLocaleTimeString(locale, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** `billable_seconds` as m:ss. The cost column stays off this screen: it is metering
 *  for the usage screen, and raw micros are not a sentence anybody should read. */
function duration(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function Tag({ label }: { label: string }) {
  return (
    <span className="border-od-border-7 text-od-muted-4 rounded-full border bg-[var(--od-raise-5)] p-[1px_8px] text-[11px] font-medium whitespace-nowrap">
      {label}
    </span>
  );
}

function ChannelBadge({
  kind,
  label,
  size = 13,
}: {
  kind: string;
  label: string;
  size?: number;
}) {
  const style = CHANNEL_STYLE[kind] ?? DEFAULT_STYLE;
  return (
    <span
      title={label}
      aria-label={label}
      className="inline-flex items-center justify-center rounded border p-[3px]"
      style={{ borderColor: style.border, background: style.background, color: style.color }}
    >
      <ChannelMark id={kind} size={size} />
    </span>
  );
}

export function Conversations({
  locale,
  t,
}: {
  locale: Locale;
  t: ConversationsDictionary;
}) {
  const [filter, setFilter] = useState<string>("all");
  const [searchText, setSearchText] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [picked, setPicked] = useState<number | null>(null);

  // Filtering and search happen on the server: the list is a page of a capped size,
  // so a browser-side filter would quietly hide older threads.
  const page = useResource<ThreadPage>(
    () =>
      conversationList({
        channel: filter === "all" ? undefined : filter,
        q: query || undefined,
        offset,
      }),
    [filter, query, offset],
  );
  const channels = useResource<ConversationChannel[]>(() => conversationChannels(), []);

  const threads = page.data?.threads ?? [];
  // The first thread of the page stands selected until somebody picks another -
  // derived, so no effect has to run to make the detail pane appear.
  const selectedId = picked !== null && threads.some((row) => row.id === picked)
    ? picked
    : (threads[0]?.id ?? null);

  const detail = useResource<ThreadDetail | null>(
    () => (selectedId === null ? Promise.resolve(null) : conversationDetail(selectedId)),
    [selectedId],
  );

  const days = threads.reduce<{ label: string; threads: Thread[] }[]>((groups, thread) => {
    const label = dayLabel(t, locale, thread.started_at);
    const group = groups.find((entry) => entry.label === label);
    if (group) group.threads.push(thread);
    else groups.push({ label, threads: [thread] });
    return groups;
  }, []);

  const searching = query !== "" || filter !== "all";
  const showFrom = (kind: "channel" | "search", value: string) => {
    setOffset(0);
    setPicked(null);
    if (kind === "channel") setFilter(value);
    else setQuery(value.trim());
  };

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
        <Sidebar locale={locale} active="conversations" />
      </div>

      <div className="mx-auto max-w-[1500px] p-[22px_28px_60px]">
        <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
          <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
            {t.title}
          </h1>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => showFrom("channel", "all")}
              className={`inline-flex cursor-pointer items-center justify-center rounded-lg border p-[8px_13px] text-[13.5px] whitespace-nowrap ${
                filter === "all"
                  ? "border-od-stroke bg-od-line-2 text-od-text"
                  : "border-od-border-7 bg-od-panel-deep-3 text-od-muted-4"
              }`}
            >
              {t.filter_all}
            </button>
            {/* One chip per channel the workspace has actually used, from the server.
                Every channel is a mark; "all" is not a channel and stays a word. */}
            {(channels.data ?? []).map((entry) => {
              const label = channelLabel(t, entry.kind);
              return (
                <button
                  key={entry.id}
                  type="button"
                  onClick={() => showFrom("channel", entry.kind)}
                  title={label}
                  aria-label={label}
                  className={`inline-flex cursor-pointer items-center justify-center rounded-lg border p-[8px_11px] text-[13.5px] whitespace-nowrap ${
                    filter === entry.kind
                      ? "border-od-stroke bg-od-line-2 text-od-text"
                      : "border-od-border-7 bg-od-panel-deep-3 text-od-muted-4"
                  }`}
                >
                  <ChannelMark id={entry.kind} size={15} />
                </button>
              );
            })}
          </div>
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            showFrom("search", searchText);
          }}
          className="mt-[14px] flex flex-wrap items-center gap-[10px]"
        >
          <input
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            placeholder={t.search_placeholder}
            aria-label={t.search_placeholder}
            maxLength={200}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 min-w-0 flex-[1_1_260px] max-w-[400px] rounded-lg border p-[9px_13px] outline-none"
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
                showFrom("search", "");
              }}
              className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-lg border bg-transparent p-[9px_14px] text-[13.5px]"
            >
              {t.search_clear}
            </button>
          ) : null}
        </form>

        {page.loading && page.data === null ? <ConversationsSkeleton /> : null}

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

        {page.data !== null && threads.length === 0 ? (
          searching ? (
            <div className="border-od-border-6 bg-od-panel-deep-2 mt-[18px] rounded-[10px] border border-dashed p-[40px_28px]">
              <h3 className="m-0 text-[18px] font-semibold">{t.no_results_title}</h3>
              <p className="text-od-muted mt-[10px] max-w-[60ch] text-pretty">
                {t.no_results_body}
              </p>
            </div>
          ) : (
            <div className="border-od-border-6 bg-od-panel-deep-2 mt-[18px] rounded-[10px] border border-dashed p-[40px_28px]">
              <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
              <p className="text-od-muted mt-[10px] max-w-[60ch] text-pretty">{t.empty_body}</p>
              <Link
                href={`/${locale}/apps`}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-4 inline-block rounded-md border p-[9px_16px] font-medium hover:no-underline"
              >
                {t.empty_action}
              </Link>
            </div>
          )
        ) : null}

        {threads.length > 0 ? (
          <div className="mt-[18px] flex flex-wrap items-start gap-5">
            <div className="border-od-line bg-od-panel-deep-3 max-w-[400px] min-w-[min(100%,300px)] flex-[1_1_320px] overflow-hidden rounded-[10px] border">
              {days.map((group) => (
                <div key={group.label}>
                  <div className="bg-od-canvas-2 sticky top-0 z-[2] flex items-center justify-between gap-[10px] border-b border-[color:var(--od-raise-6)] p-[8px_16px]">
                    <span className="text-od-faint text-[11px] font-semibold tracking-[.09em] uppercase">
                      {group.label}
                    </span>
                    <span className="text-od-faint-2 text-[11.5px]">
                      {group.threads.length === 1
                        ? t.conversations_one
                        : interpolate(t.conversations_many, { count: group.threads.length })}
                    </span>
                  </div>

                  {group.threads.map((thread) => {
                    const active = thread.id === selectedId;
                    const state = stateOf(thread);
                    return (
                      <button
                        key={thread.id}
                        type="button"
                        onClick={() => setPicked(thread.id)}
                        className="block w-full cursor-pointer border-0 border-b border-solid border-[color:var(--od-raise-6)] bg-transparent p-[13px_16px] text-start text-[14px] leading-[1.45] text-inherit"
                        style={{
                          background: active ? "var(--od-raise)" : "transparent",
                          borderInlineStart: `2px solid ${active ? "var(--od-violet)" : "transparent"}`,
                        }}
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          {/* In the list the mark stands alone. The name is repeated on
                              every row and says nothing the logo does not; dropping it
                              gives the sentence underneath the width instead. It stays
                              on the label, so a screen reader and a hover still say
                              which channel this is. */}
                          <ChannelBadge
                            kind={thread.channel}
                            label={channelLabel(t, thread.channel)}
                          />
                          <span dir="auto" className="text-od-text font-medium text-pretty">
                            {thread.who_name ?? thread.who ?? t.thread_visitor}
                          </span>
                          <span
                            dir="ltr"
                            className="mono ltr-data text-od-faint ms-auto text-[11.5px]"
                          >
                            {clock(locale, thread.started_at)}
                          </span>
                        </div>

                        {/* What the customer wrote, verbatim - never translated, and
                            never forced into one direction: `ltr` renders an Arabic
                            message backwards, and the page's own direction puts an
                            English sentence's full stop at the front. `auto` reads the
                            direction off the first strong character, which is the only
                            rule that is right for text whose language nobody declared. */}
                        {thread.preview ? (
                          <div
                            dir="auto"
                            className="text-od-muted-2 mt-[6px] line-clamp-2 text-start text-[13px] text-pretty"
                          >
                            {thread.preview}
                          </div>
                        ) : null}

                        <div className="mt-[7px] flex flex-wrap items-center gap-2">
                          <span
                            className="text-[12px] font-medium"
                            style={{
                              color: state.settled ? "var(--od-faint)" : "var(--od-amber-text)",
                            }}
                          >
                            {t[state.label]}
                          </span>
                          {thread.intent ? <Tag label={thread.intent} /> : null}
                        </div>
                      </button>
                    );
                  })}
                </div>
              ))}

              {offset > 0 || page.data?.has_more ? (
                <div className="flex items-center justify-between gap-[10px] p-[10px_16px]">
                  <button
                    type="button"
                    disabled={offset === 0}
                    onClick={() => {
                      setOffset(Math.max(0, offset - THREADS_PAGE));
                      setPicked(null);
                    }}
                    className="border-od-border-7 text-od-muted-4 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] disabled:cursor-default disabled:opacity-40"
                  >
                    {t.page_newer}
                  </button>
                  <button
                    type="button"
                    disabled={!page.data?.has_more}
                    onClick={() => {
                      setOffset(offset + THREADS_PAGE);
                      setPicked(null);
                    }}
                    className="border-od-border-7 text-od-muted-4 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] disabled:cursor-default disabled:opacity-40"
                  >
                    {t.page_older}
                  </button>
                </div>
              ) : null}
            </div>

            <div className="border-od-line bg-od-panel-deep-3 flex min-w-[min(100%,420px)] flex-[2_1_460px] flex-col overflow-hidden rounded-[10px] border">
              {detail.data !== null ? (
                <ThreadPane locale={locale} t={t} thread={detail.data} />
              ) : detail.error !== null ? (
                <div className="p-[18px]">
                  <h3 className="m-0 text-[15px] font-semibold text-[color:var(--od-red-text-3)]">
                    {detail.error.kind === "offline"
                      ? t.error_offline_title
                      : t.error_failed_title}
                  </h3>
                  <button
                    type="button"
                    onClick={detail.reload}
                    className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
                  >
                    {t.retry}
                  </button>
                </div>
              ) : (
                <div
                  className="border-od-raise-12 m-[18px] h-[420px] rounded-[10px] border"
                  style={shimmer}
                />
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ThreadPane({
  locale,
  t,
  thread,
}: {
  locale: Locale;
  t: ConversationsDictionary;
  thread: ThreadDetail;
}) {
  return (
    <>
      <div className="border-od-line bg-od-canvas-2 flex flex-wrap items-center justify-between gap-x-[18px] gap-y-3 border-b p-[14px_18px]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-[9px]">
            <span className="text-od-text text-[16px] font-semibold">
              {thread.who_name ?? thread.who ?? t.thread_visitor}
            </span>
            <ChannelBadge
              kind={thread.channel}
              label={channelLabel(t, thread.channel)}
              size={14}
            />
            {thread.intent ? <Tag label={thread.intent} /> : null}
          </div>
          <div dir="ltr" className="mono ltr-data text-od-muted-5 mt-[3px] text-[12.5px]">
            {new Date(thread.started_at).toLocaleDateString(locale, {
              day: "numeric",
              month: "long",
              year: "numeric",
            })}{" "}
            · {clock(locale, thread.started_at)}
            {" · "}
            {thread.message_count === 1
              ? t.messages_one
              : interpolate(t.messages_many, { count: thread.message_count })}
          </div>
        </div>
      </div>

      {thread.summary ? (
        <div className="border-od-line border-b p-[14px_18px]">
          <div className="text-od-faint text-[11px] font-semibold tracking-[.09em] uppercase">
            {t.summary_label}
          </div>
          {/* The agent's own words about the conversation, in whatever language it
              was held in - so the direction is read off the text, not off the page. */}
          <p
            dir="auto"
            className="text-od-muted m-0 mt-[6px] max-w-[70ch] text-[13.5px] text-pretty"
          >
            {thread.summary}
          </p>
        </div>
      ) : null}

      <div className="flex flex-col gap-3 p-[18px]">
        {thread.messages.map((message) => (
          <MessageBubble key={message.id} locale={locale} t={t} thread={thread} message={message} />
        ))}
      </div>

      {thread.call ? (
        <div className="border-od-line mt-auto border-t p-[14px_18px]">
          <div className="text-od-faint text-[11px] font-semibold tracking-[.09em] uppercase">
            {t.call_label}
          </div>
          <div className="mt-[8px] flex flex-wrap items-center gap-x-[18px] gap-y-[6px] text-[13px]">
            {thread.call.from_e164 ? (
              <span dir="ltr" className="mono ltr-data text-od-muted-2">
                {thread.call.from_e164}
              </span>
            ) : null}
            {thread.call.billable_seconds !== null ? (
              <span className="text-od-muted-2">
                {t.call_duration}{" "}
                <span dir="ltr" className="mono ltr-data">
                  {duration(thread.call.billable_seconds)}
                </span>
              </span>
            ) : null}
            <span className="text-od-muted-5">
              {thread.call.has_recording ? t.call_recording_yes : t.call_recording_no}
            </span>
          </div>
        </div>
      ) : null}
    </>
  );
}

function MessageBubble({
  locale,
  t,
  thread,
  message,
}: {
  locale: Locale;
  t: ConversationsDictionary;
  thread: ThreadDetail;
  message: ThreadMessage;
}) {
  const them = message.speaker === "caller";
  const meta: string[] = [clock(locale, thread.started_at, message.ts_ms)];
  if (message.speaker === "human") meta.push(t.speaker_human);
  // Confidence and language exist only on spoken lines; their absence IS the signal
  // that a line was typed, so nothing is drawn for it.
  if (message.stt_confidence !== null)
    meta.push(`${Math.round(message.stt_confidence * 100)}%`);
  if (message.language !== null) meta.push(message.language);

  return (
    <div className={`flex flex-col ${them ? "items-start" : "items-end"}`}>
      {message.is_whisper ? (
        <div className="text-[11px] font-medium text-[color:var(--od-amber-text)]">
          {t.whisper_label}
        </div>
      ) : null}
      <div
        dir="ltr"
        className="max-w-[84%] rounded-[10px] border p-[11px_14px] text-start text-[15px] leading-[1.65] text-pretty"
        style={{
          borderColor: them
            ? "var(--od-border-6)"
            : message.is_whisper
              ? "var(--od-amber-border)"
              : "var(--od-violet-border)",
          background: them
            ? "var(--od-raise)"
            : message.is_whisper
              ? "var(--od-amber-bg)"
              : "rgba(139,124,255,.10)",
          color: them
            ? "var(--od-text-4)"
            : message.is_whisper
              ? "var(--od-amber-text)"
              : "var(--od-violet-4)",
        }}
      >
        {message.text}
      </div>
      <div dir="ltr" className="mono ltr-data text-od-faint-2 mt-1 text-[11.5px]">
        {meta.join(" · ")}
      </div>
    </div>
  );
}

const shimmer = {
  background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
  backgroundSize: "420px 100%",
  animation: "od-shimmer 1.4s linear infinite",
};

function ConversationsSkeleton() {
  return (
    <div className="mt-[18px] flex flex-wrap gap-5">
      <div className="flex max-w-[380px] flex-[1_1_320px] flex-col gap-[10px]">
        {[0, 1, 2, 3, 4, 5].map((index) => (
          <div
            key={index}
            className="border-od-raise-12 h-[74px] rounded-[10px] border"
            style={shimmer}
          />
        ))}
      </div>
      <div
        className="border-od-raise-12 h-[520px] flex-[2_1_420px] rounded-[10px] border"
        style={shimmer}
      />
    </div>
  );
}
