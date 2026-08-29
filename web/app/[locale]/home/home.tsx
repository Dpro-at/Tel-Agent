"use client";

import Link from "next/link";

import { Sidebar } from "@/components/shell/sidebar";
import {
  conversationList,
  homeSummary,
  type HomeSummary,
  type Thread,
  type ThreadHandling,
  type ThreadPage,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { HomeDictionary } from "./page";

type Key = keyof HomeDictionary;

/** How many threads the first screen shows before sending the reader to the full list. */
const RECENT = 5;

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

/**
 * The instant the reader's day began, in their own timezone.
 *
 * Sent to the server rather than decided there: a workspace has no timezone, so a
 * business in Vienna asking at 00:30 would otherwise be told about a day that ended
 * ninety minutes ago.
 */
function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function greetingFor(t: HomeDictionary): string {
  const hour = new Date().getHours();
  if (hour < 12) return t.greeting_morning;
  if (hour < 18) return t.greeting_afternoon;
  return t.greeting_evening;
}

/** `HH:MM` in the reader's timezone, which is the only one they can act in. */
function clock(iso: string, locale: Locale): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", hour12: false });
}

/**
 * "14 conversations today · 11 answered by the agent".
 *
 * The second clause appears only when `by_agent` is a number. Null means nothing has
 * recorded who took a conversation, and drawing that as a zero would state as a
 * measurement something that was never measured.
 */
function today(data: HomeSummary, t: HomeDictionary): string {
  if (data.conversations === 0) return t.today_none;
  const counted =
    data.conversations === 1
      ? t.today_one
      : interpolate(t.today_many, { count: data.conversations });
  if (data.by_agent === null) return counted;
  return `${counted} · ${interpolate(t.today_by_agent, { count: data.by_agent })}`;
}

export function Home({ locale, t }: { locale: Locale; t: HomeDictionary }) {
  // Two requests rather than one endpoint returning both: the recent threads are
  // assembled by the conversations route, which knows how to do it and is already
  // scoped by workspace. A second place to build them is a second place to forget.
  const summary = useResource<HomeSummary>(() => homeSummary(startOfToday()));
  const recent = useResource<ThreadPage>(() => conversationList({ limit: RECENT }));

  const retry = () => {
    summary.reload();
    recent.reload();
  };

  // Offline is about the connection, so either request seeing it means the whole screen
  // is stale - which is worth saying once at the top rather than twice in the middle.
  const offline = summary.error?.kind === "offline" || recent.error?.kind === "offline";
  const refused =
    (summary.error !== null && summary.error.kind !== "offline") ||
    (recent.error !== null && recent.error.kind !== "offline");
  const firstLoad =
    summary.data === null && recent.data === null && !summary.error && !recent.error;

  const threads = recent.data?.threads ?? [];
  const waiting = summary.data?.waiting ?? 0;

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="home" />
      </div>

      {offline ? <OfflineBanner t={t} onRetry={retry} /> : null}

      <div className="mx-auto max-w-[1240px] p-[22px_28px_60px]">
        {refused ? (
          <ServerError t={t} onRetry={retry} />
        ) : firstLoad ? (
          <HomeSkeleton />
        ) : (
          <div>
            <div className="flex flex-wrap items-baseline justify-between gap-x-5 gap-y-[14px]">
              <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                {greetingFor(t)}
              </h1>
              <div className="text-od-muted-4">{summary.data ? today(summary.data, t) : null}</div>
            </div>

            {waiting > 0 ? (
              <Link
                href={`/${locale}/notifications`}
                className="border-od-red-border-3 bg-od-red-bg-4 hover:bg-od-red-bg-2 mt-[18px] flex flex-wrap items-center gap-x-4 gap-y-[10px] rounded-[10px] border p-[13px_16px] hover:no-underline"
              >
                <span
                  className="size-[9px] flex-none rounded-full bg-[#F0605E]"
                  style={{ animation: "od-ring 1.6s ease-out infinite" }}
                />
                <span className="min-w-0 flex-[1_1_260px]">
                  <span className="block text-[14.5px] font-semibold text-[color:var(--od-red-text-3)]">
                    {waiting === 1 ? t.waiting_one : interpolate(t.waiting_many, { count: waiting })}
                  </span>
                  <span className="mt-[2px] block text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
                    {t.waiting_body}
                  </span>
                </span>
                <span className="flex-none text-[13px] whitespace-nowrap text-[color:var(--od-red-text-2)]">
                  {t.waiting_link}
                </span>
              </Link>
            ) : null}

            <section className="mt-5 flex flex-wrap items-start gap-4">
              <div className="order-1 min-w-[min(100%,420px)] flex-[2_1_460px]">
                <div className="mb-[10px] flex flex-wrap items-baseline justify-between gap-[10px]">
                  <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
                    {t.recent_heading}
                  </h2>
                  <Link
                    href={`/${locale}/conversations`}
                    className="text-od-violet text-[13px] hover:underline"
                  >
                    {t.all_conversations}
                  </Link>
                </div>

                {threads.length === 0 ? (
                  <div className="border-od-border-6 bg-od-panel-deep-2 rounded-[10px] border border-dashed p-[30px]">
                    <h3 className="m-0 text-[17px] font-semibold">{t.empty_title}</h3>
                    <p className="text-od-muted mt-2 max-w-[56ch] text-pretty">{t.empty_body}</p>
                  </div>
                ) : (
                  <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
                    {threads.map((thread) => (
                      <Row key={thread.id} thread={thread} locale={locale} t={t} />
                    ))}
                  </div>
                )}
              </div>

              <DialCard t={t} />
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ thread, locale, t }: { thread: Thread; locale: Locale; t: HomeDictionary }) {
  const badge = thread.handling ? BADGES[thread.handling as ThreadHandling] : null;
  const length =
    thread.message_count === 1
      ? t.messages_one
      : interpolate(t.messages_many, { count: thread.message_count });

  return (
    <Link
      href={`/${locale}/conversations`}
      className="hover:bg-od-raise grid items-center gap-[18px] border-b border-[color:var(--od-raise-6)] p-[10px_16px] hover:no-underline"
      style={{
        gridTemplateColumns:
          "minmax(56px, max-content) minmax(0,1fr) minmax(96px, max-content) minmax(48px, max-content)",
      }}
    >
      <span dir="ltr" className="mono ltr-data text-od-muted-5 text-[12.5px]">
        {clock(thread.started_at, locale)}
      </span>

      {/* `auto`, not a fixed direction: a name and a visitor's own words can be in any
          script, and the browser reads the direction off the first strong character.
          Forced to `ltr` an Arabic message renders backwards; left to the page's own
          direction, an English sentence puts its full stop at the front. */}
      <div className="min-w-0">
        <div dir="auto" className="text-od-text truncate font-medium">
          {thread.who_name ?? thread.who ?? t.who_unknown}
        </div>
        {/* The last line of the thread, which is what a person recognises it by. The
            fixture this replaced showed a translated intent; nothing writes
            `conversations.intent` yet, and it is free text rather than a fixed list. */}
        {thread.preview ? (
          <div dir="auto" className="text-od-muted-5 mt-[2px] truncate text-[13px]">
            {thread.preview}
          </div>
        ) : null}
      </div>

      {/* Drawn only when something recorded who took it. */}
      {badge ? (
        <span
          className="inline-flex justify-self-start rounded-md border p-[3px_10px] text-[12.5px] font-medium whitespace-nowrap"
          style={{ borderColor: badge.border, background: badge.background, color: badge.color }}
        >
          {t[badge.label]}
        </span>
      ) : (
        <span />
      )}

      {/* Labelled, not bare. The column held a call's duration in the fixture, so an
          unexplained number in the same place reads as one - and a thread's length is
          the honest thing to show for a channel that has no duration. */}
      <span
        dir="ltr"
        title={length}
        aria-label={length}
        className="mono ltr-data text-od-muted text-[13px]"
      >
        {thread.message_count}
      </span>
    </Link>
  );
}

function OfflineBanner({ t, onRetry }: { t: HomeDictionary; onRetry: () => void }) {
  return (
    <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-4">
      <span
        className="size-[10px] flex-none rounded-full bg-[#F0605E]"
        style={{ animation: "od-ring 1.6s ease-out infinite" }}
      />
      <div className="min-w-[240px] flex-[1_1_340px]">
        <div className="text-[16px] font-semibold text-[color:var(--od-red-text)]">
          {t.offline_title}
        </div>
        <div className="mt-[3px] text-[color:var(--od-red-text-2)]">{t.offline_body}</div>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[9px_15px] font-medium text-[color:var(--od-red-text-3)]"
      >
        {t.offline_retry}
      </button>
    </div>
  );
}

/**
 * §A6.2 calls the dial card "deliberately minor" — the user is not a switchboard
 * operator. It sits in the side column and never takes the lead.
 *
 * **Nothing here dials.** The phone is the last channel built, so the keypad is a
 * shape rather than a control: it says what it is waiting for instead of accepting
 * digits for a call that cannot be placed. Keeping the block means the column it
 * occupies is settled now, rather than being designed a second time.
 */
function DialCard({ t }: { t: HomeDictionary }) {
  return (
    <div className="border-od-line bg-od-panel-deep-3 order-2 max-w-[380px] min-w-[min(100%,300px)] flex-[1_1_320px] rounded-[10px] border p-[18px]">
      <h2 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
        {t.dial_heading}
      </h2>

      {/* A telephone keypad reads 1-2-3 left to right on every handset ever made, so it
          never mirrors with the page. `aria-hidden` because it is an illustration: a
          screen reader announcing twelve unusable buttons would be reading furniture. */}
      <div dir="ltr" aria-hidden className="mt-[13px] grid grid-cols-3 gap-2 opacity-40">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"].map((digit) => (
          <div
            key={digit}
            className="border-od-border-7 bg-od-canvas-2 text-od-faint-2 flex items-center justify-center rounded-lg border p-[13px_0]"
          >
            <span className="mono ltr-data text-[18px]">{digit}</span>
          </div>
        ))}
      </div>

      <p className="text-od-faint mt-[13px] mb-0 text-[12.5px] text-pretty">{t.dial_waiting}</p>
    </div>
  );
}

function ServerError({ t, onRetry }: { t: HomeDictionary; onRetry: () => void }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.error_label}
        </div>
        <h2 className="mt-[18px] mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">{t.error_body}</p>
        <button
          type="button"
          onClick={onRetry}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-5 cursor-pointer rounded-md border p-[9px_16px] font-medium"
        >
          {t.error_reload}
        </button>
      </div>
    </div>
  );
}

const SHIMMER = {
  background: "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
  backgroundSize: "420px 100%",
  animation: "od-shimmer 1.4s linear infinite",
};

function HomeSkeleton() {
  return (
    <div>
      <div className="border-od-raise-12 h-[34px] w-[220px] rounded-md border" style={SHIMMER} />
      <div className="mt-5 flex flex-wrap items-start gap-4">
        <div className="order-1 flex min-w-[min(100%,420px)] flex-[2_1_460px] flex-col gap-[10px]">
          {[0, 1, 2, 3, 4].map((index) => (
            <div
              key={index}
              className="border-od-raise-12 h-[54px] rounded-[10px] border"
              style={SHIMMER}
            />
          ))}
        </div>
        <div
          className="border-od-raise-12 order-2 h-[320px] max-w-[380px] min-w-[min(100%,300px)] flex-[1_1_320px] rounded-[10px] border"
          style={SHIMMER}
        />
      </div>
    </div>
  );
}
