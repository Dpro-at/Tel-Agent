"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ChannelMark } from "@/components/shell/channel-mark";
import { Sidebar } from "@/components/shell/sidebar";
import {
  ApiError,
  conversationDetail,
  conversationList,
  sendWhisper,
  type Thread,
  type ThreadDetail,
  type ThreadMessage,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { LiveDictionary } from "./page";

/**
 * How often the two lists ask again.
 *
 * **This page does not stream, and it says so.** The reply streams to the *widget* over
 * SSE on a public path; there is no live feed into the dashboard, and `api/main.py`
 * serves no websocket route at all — the guard for one exists and has nothing to guard.
 * Drawing a word-by-word transcript here would be drawing the thing §A6.7 asks for
 * rather than the thing that exists.
 *
 * Five seconds is chosen against what the screen is for. A whisper is written while a
 * customer is waiting for an answer, so the operator needs to see the line they are
 * answering; five seconds is inside that window and is two requests a minute per open
 * screen, which is nothing.
 */
const REFRESH_MS = 5000;

/** Ask again on a timer, and stop when the tab is not being looked at.
 *
 * `useResource` keeps the previous data until the new data lands, so a poll never
 * blanks the screen — which is the property that makes polling tolerable to read.
 */
function usePolling(reload: () => void, active = true): void {
  const latest = useRef(reload);
  useEffect(() => {
    latest.current = reload;
  });

  useEffect(() => {
    if (!active) return;

    const timer = setInterval(() => {
      // A background tab polling a server for hours is a cost nobody asked for, and
      // nobody is reading the answer anyway.
      if (document.visibilityState === "visible") latest.current();
    }, REFRESH_MS);

    // **And ask again the moment the tab comes back.** Skipping the poll while hidden
    // is only half the rule: without this, an operator returning to the tab reads
    // whatever was true when they left it for up to another interval — on a screen
    // whose entire purpose is what is happening right now. It is also what makes the
    // guard above safe to keep, since a tab that was hidden for an hour catches up in
    // one request rather than staying wrong.
    const wake = () => {
      if (document.visibilityState === "visible") latest.current();
    };
    document.addEventListener("visibilitychange", wake);

    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", wake);
    };
  }, [active]);
}

/** The time of day, as the reader's locale writes it. */
function atTime(iso: string, locale: Locale): string {
  return new Date(iso).toLocaleTimeString(locale === "ar" ? "ar" : locale, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Whoever is on the other end, in words.
 *
 * **`thread.who` is never shown for a web thread, and that is not cosmetic.** On the
 * web channel `external_id` is the visitor's own resume handle - the unguessable string
 * their widget sends to continue the same conversation - and the API returns it as
 * `who`. Printed as a headline it is both meaningless to read and a credential on a
 * screen: whoever sees it can continue that visitor's thread. The phonebook name is
 * shown when there is one, and "website visitor" when there is not.
 *
 * A number on a `phone` thread is a different thing entirely - it *is* how the person
 * is identified - so it is shown as it always was.
 */
function who(thread: Thread, t: LiveDictionary): string {
  if (thread.who_name) return thread.who_name;
  return thread.channel === "web" ? t.visitor : (thread.who ?? t.visitor);
}

/**
 * One line of the transcript.
 *
 * A whisper is drawn in its own colour and named as one, per §A6.4: the customer never
 * saw it, and a screen that drew it like any other line would be showing a conversation
 * that did not happen. It carries the name of whoever wrote it, which is what
 * `messages.author_user_id` exists for — `speaker` says `human`, and on a desk of four
 * that is not an answer.
 *
 * **No per-line clock, deliberately.** `messages.ts_ms` is documented as milliseconds
 * since the conversation started and written by `api/routes/public_chat.py` as epoch
 * milliseconds, and the archive screen reads it the documented way — so a message sent
 * today renders there as 2083. Until that is settled and backfilled, this screen shows
 * no per-line time rather than picking a reading and being confidently wrong. On a
 * conversation that is happening *now* the clock earns very little anyway.
 */
function Line({ message, t }: { message: ThreadMessage; t: LiveDictionary }) {
  const fromThem = message.speaker === "caller";
  const label = message.is_whisper
    ? t.whisper_label
    : fromThem
      ? t.speaker_caller
      : message.speaker === "agent"
        ? t.speaker_agent
        : t.speaker_human;

  return (
    <div className={`flex flex-col ${fromThem ? "items-start" : "items-end"}`}>
      <div
        className="text-[11px] font-medium"
        style={{
          color: message.is_whisper ? "var(--od-amber-text)" : "var(--od-faint-2)",
        }}
      >
        {message.is_whisper && message.author
          ? interpolate(t.whisper_by, { who: message.author })
          : label}
      </div>
      {/* `auto`, not `ltr`. What a customer typed is in a language nobody here chose,
          and an English sentence inside an Arabic page has its full stop thrown to the
          wrong end without this. `auto` lets the browser read the first strong
          character and get both cases right; hardcoding `ltr`, as the archive screen
          does, only moves the bug to the Arabic visitor. */}
      <div
        dir="auto"
        className="mt-1 max-w-[84%] rounded-[10px] border p-[11px_14px] text-start text-[15px] leading-[1.65] text-pretty"
        style={{
          borderColor: message.is_whisper
            ? "var(--od-amber-border)"
            : fromThem
              ? "var(--od-border-6)"
              : "var(--od-violet-border)",
          background: message.is_whisper
            ? "var(--od-amber-bg)"
            : fromThem
              ? "var(--od-raise)"
              : "rgba(139,124,255,.10)",
          color: message.is_whisper
            ? "var(--od-amber-text)"
            : fromThem
              ? "var(--od-text-4)"
              : "var(--od-violet-4)",
        }}
      >
        {message.text}
      </div>
    </div>
  );
}

/** The box that writes into a conversation while it is still running. */
function WhisperBox({
  conversationId,
  onSent,
  t,
}: {
  conversationId: number;
  onSent: () => void;
  t: LiveDictionary;
}) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const body = text.trim();
    if (!body) return;
    setPending(true);
    setError(null);
    try {
      await sendWhisper(conversationId, body);
      setText("");
      onSent();
    } catch (thrown) {
      if (thrown instanceof ApiError && thrown.status === 403) setError(t.not_allowed);
      else if (thrown instanceof ApiError && thrown.code === "conversation_closed")
        setError(t.ended_note);
      else setError(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[14px_16px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.whisper}
        </h3>
      </div>
      <p className="text-od-faint mt-[6px] mb-0 max-w-[64ch] text-[12.5px] text-pretty">
        {t.whisper_note}
      </p>

      <form
        className="mt-3 flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <textarea
          rows={2}
          value={text}
          maxLength={2000}
          placeholder={t.whisper_placeholder}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // The operator is typing under time pressure. Enter sends; a deliberate
            // newline still needs Shift.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          className="border-od-border-6 bg-od-canvas-2 text-od-text-2 min-w-[240px] flex-[1_1_320px] resize-y rounded-[7px] border p-[10px_12px] text-[14px]"
        />
        <button
          type="submit"
          disabled={pending || text.trim() === ""}
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[10px_18px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {pending ? t.whisper_sending : t.whisper_send}
        </button>
      </form>

      {error === null ? null : (
        <p className="mt-2 mb-0 text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
          {error}
        </p>
      )}
    </div>
  );
}

/** The thread on the right: what has been said, and the box that adds to it. */
function OpenThread({
  conversationId,
  locale,
  t,
}: {
  conversationId: number;
  locale: Locale;
  t: LiveDictionary;
}) {
  const thread = useResource<ThreadDetail>(
    () => conversationDetail(conversationId),
    [conversationId],
  );
  usePolling(thread.reload);

  if (thread.data === null && thread.loading) {
    return <p className="text-od-muted-5 m-0 text-[13px]">{t.loading}</p>;
  }
  if (thread.data === null) {
    return (
      <div>
        <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
          {thread.error?.message ?? t.failed}
        </p>
        <button
          type="button"
          onClick={thread.reload}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
        >
          {t.retry}
        </button>
      </div>
    );
  }

  const ended = thread.data.status !== "open";

  return (
    <div className="flex flex-col gap-4">
      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[16px_18px]">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
          <span className="text-od-text flex items-center gap-2 text-[16px] font-semibold">
            <ChannelMark id={thread.data.channel} size={14} />
            {who(thread.data, t)}
          </span>
          <Link
            href={`/${locale}/conversations`}
            className="text-od-muted-5 hover:text-od-text-2 text-[12.5px]"
          >
            {t.open_archive}
          </Link>
        </div>
        <div className="text-od-faint mt-[5px] text-[12.5px]">
          {interpolate(t.started, { when: atTime(thread.data.started_at, locale) })}
        </div>
      </div>

      {/* A conversation can end between two polls. Saying so is the honest answer to
          a whisper box that would otherwise take a line the server will refuse. */}
      {ended ? (
        <div
          className="rounded-[10px] border p-[14px_16px]"
          style={{ borderColor: "var(--od-border-7)", background: "var(--od-raise-5)" }}
        >
          <div className="text-od-text-3 text-[14px] font-medium">{t.ended_title}</div>
          <p className="text-od-muted-5 mt-[5px] mb-0 max-w-[64ch] text-[12.5px] text-pretty">
            {t.ended_note}
          </p>
        </div>
      ) : (
        <WhisperBox conversationId={conversationId} onSent={thread.reload} t={t} />
      )}

      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[16px_18px]">
        <h3 className="text-od-muted-4 m-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.transcript}
        </h3>
        <div className="flex flex-col gap-3">
          {thread.data.messages.map((message) => (
            <Line key={message.id} message={message} t={t} />
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * The live screen — §A6.7, with everything it cannot do removed.
 *
 * **What went, and why.** The drawing this replaces was a phone screen: dial pad,
 * device picker (desk, DECT, this computer), *Listen in*, *Take over*, *Hand off*,
 * *End call*, a registration-lost banner and a tools rail. None of it has an endpoint,
 * and most of it has no milestone either — `api/` never touches audio, nothing in this
 * product can place or join a call, and the phone is Milestone 11. §A6.7 itself orders
 * these: *"Whisper — highest value, lowest complexity. Build first."* This is that one,
 * and only that one.
 *
 * The screen is therefore about **conversations**, not calls. A call will be one when
 * there are calls (D-017 made a call a conversation), and this screen will not need
 * changing for it — a `phone` thread already lists here the moment one exists.
 */
export function Live({ locale, t }: { locale: Locale; t: LiveDictionary }) {
  const open = useResource(() => conversationList({ status: "open", limit: 25 }), []);
  usePolling(open.reload);

  const [selected, setSelected] = useState<number | null>(null);
  const threads = open.data?.threads ?? [];

  // **The selection outlives the list on purpose.** A conversation ends the moment the
  // customer is done, which is often while somebody is still reading it - and clearing
  // the pane then would take the transcript away mid-sentence, from the one person who
  // might have to act on it. It stays, and says it has ended instead. (Written the
  // other way first: the pane cleared itself, which also made the "has ended" panel
  // unreachable - a state drawn for a case that could not occur.)
  const current = selected;

  return (
    <div className="bg-od-canvas text-od-text flex min-h-screen">
      <Sidebar locale={locale} active="live" />

      <main className="min-w-0 flex-1 p-[26px_30px_60px]">
        <header className="mb-5">
          <h1 className="m-0 text-[26px] font-semibold">{t.title}</h1>
          <p className="text-od-muted-2 mt-[6px] mb-0 max-w-[76ch] text-pretty">{t.lead}</p>
          {/* Said out loud rather than implied by a spinner: this is not a stream. */}
          <p className="text-od-faint mt-[6px] mb-0 max-w-[76ch] text-[12.5px] text-pretty">
            {t.refresh_note}
          </p>
        </header>

        <div className="flex flex-wrap items-start gap-5">
          <section className="min-w-[260px] flex-[1_1_320px]">
            <h2 className="text-od-muted-4 m-0 mb-[10px] text-[13px] font-semibold tracking-[.07em] uppercase">
              {t.open_now}
            </h2>

            {open.data === null && open.loading ? (
              <p className="text-od-muted-5 m-0 text-[13px]">{t.loading}</p>
            ) : open.data === null ? (
              <div>
                <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
                  {open.error?.message ?? t.failed}
                </p>
                <button
                  type="button"
                  onClick={open.reload}
                  className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
                >
                  {t.retry}
                </button>
              </div>
            ) : threads.length === 0 ? (
              <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
                <div className="text-od-text-3 text-[14px] font-medium">{t.nobody_live}</div>
                <p className="text-od-muted-5 mt-[5px] mb-0 max-w-[52ch] text-[12.5px] text-pretty">
                  {t.nobody_live_note}
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {threads.map((thread) => {
                  const on = thread.id === current;
                  return (
                    <button
                      key={thread.id}
                      type="button"
                      onClick={() => setSelected(thread.id)}
                      className="cursor-pointer rounded-[10px] border p-[12px_14px] text-start"
                      style={{
                        borderColor: on ? "var(--od-violet)" : "var(--od-line)",
                        background: on ? "var(--od-raise-10)" : "var(--od-panel-deep-3)",
                      }}
                    >
                      <span className="text-od-text flex items-center gap-2 font-medium">
                        <ChannelMark id={thread.channel} size={13} />
                        {who(thread, t)}
                      </span>
                      {thread.preview ? (
                        <span
                          dir="auto"
                          className="text-od-muted-5 mt-[4px] line-clamp-2 block text-start text-[12.5px] text-pretty"
                        >
                          {thread.preview}
                        </span>
                      ) : null}
                      <span className="text-od-faint mt-[5px] block text-[12px]">
                        {interpolate(t.started, {
                          when: atTime(thread.started_at, locale),
                        })}
                        {" · "}
                        {thread.message_count === 1
                          ? t.lines_one
                          : interpolate(t.lines_many, { count: thread.message_count })}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>

          <section className="min-w-[320px] flex-[2_1_480px]">
            {current === null ? (
              <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
                <p className="text-od-muted-5 m-0 max-w-[52ch] text-[13px] text-pretty">
                  {t.pick_one}
                </p>
              </div>
            ) : (
              <OpenThread conversationId={current} locale={locale} t={t} />
            )}
          </section>
        </div>
      </main>
    </div>
  );
}
