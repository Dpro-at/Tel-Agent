"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ChannelMark } from "@/components/shell/channel-mark";
import { Sidebar } from "@/components/shell/sidebar";
import {
  ApiError,
  conversationDetail,
  conversationList,
  resumeAgent,
  sendReply,
  sendWhisper,
  takeOver,
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

/**
 * §A6.4's takeover markers, derived rather than stored.
 *
 * A non-whisper `human` line *is* the takeover speaking — no separate event row
 * exists, and inventing one would put a line into the transcript that nobody said.
 * So the divider is drawn where the record shows the voice changing: before the
 * first `human` line after the agent (or the start), and before the first `agent`
 * line after a human. The name on the join is the line's own author.
 */
function marker(
  message: ThreadMessage,
  previousBusinessSpeaker: string | null,
  t: LiveDictionary,
): string | null {
  if (message.is_whisper || message.speaker === "caller") return null;
  if (message.speaker === "human" && previousBusinessSpeaker !== "human") {
    return interpolate(t.marker_joined, { who: message.author ?? t.speaker_human });
  }
  if (message.speaker === "agent" && previousBusinessSpeaker === "human") {
    return t.marker_resumed;
  }
  return null;
}

function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3" role="separator">
      <span className="bg-od-border-6 h-px flex-1" />
      <span className="text-od-muted-4 text-[11.5px] font-medium tracking-[.04em]">
        {label}
      </span>
      <span className="bg-od-border-6 h-px flex-1" />
    </div>
  );
}

/**
 * The box that writes into a conversation while it is still running — the whisper
 * outside a takeover, the reply inside one. Same box, different endpoint and words,
 * and the words are what stop somebody coaching the agent when they are in fact
 * speaking to the customer.
 */
function Composer({
  heading,
  note,
  placeholder,
  sendLabel,
  sendingLabel,
  notAllowed,
  endedNote,
  submit,
  onSent,
}: {
  heading: string;
  note: string;
  placeholder: string;
  sendLabel: string;
  sendingLabel: string;
  notAllowed: string;
  endedNote: string;
  submit: (text: string) => Promise<unknown>;
  onSent: () => void;
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
      await submit(body);
      setText("");
      onSent();
    } catch (thrown) {
      if (thrown instanceof ApiError && thrown.status === 403) setError(notAllowed);
      else if (thrown instanceof ApiError && thrown.code === "conversation_closed")
        setError(endedNote);
      else setError(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[14px_16px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {heading}
        </h3>
      </div>
      <p className="text-od-faint mt-[6px] mb-0 max-w-[64ch] text-[12.5px] text-pretty">
        {note}
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
          placeholder={placeholder}
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
          {pending ? sendingLabel : sendLabel}
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

/**
 * The switch between the two modes — §A6.7's *Take over*, large and unambiguous.
 *
 * One button, whichever direction applies. The error handling is the composer's:
 * a viewer is told the role that is missing, and a thread that ended between polls
 * says so rather than failing namelessly.
 */
function HandlingSwitch({
  conversationId,
  taken,
  onChanged,
  t,
}: {
  conversationId: number;
  taken: boolean;
  onChanged: () => void;
  t: LiveDictionary;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function flip() {
    setPending(true);
    setError(null);
    try {
      await (taken ? resumeAgent(conversationId) : takeOver(conversationId));
      onChanged();
    } catch (thrown) {
      if (thrown instanceof ApiError && thrown.status === 403) setError(t.not_allowed_intervene);
      else if (thrown instanceof ApiError && thrown.code === "conversation_closed")
        setError(t.ended_note);
      else setError(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        disabled={pending}
        onClick={() => void flip()}
        className="cursor-pointer rounded-[7px] border p-[10px_18px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        style={
          taken
            ? {
                borderColor: "var(--od-stroke)",
                background: "var(--od-raise-10)",
                color: "var(--od-text)",
              }
            : {
                borderColor: "var(--od-amber-border)",
                background: "var(--od-amber-bg)",
                color: "var(--od-amber-text)",
              }
        }
      >
        {pending
          ? taken
            ? t.handing_back
            : t.taking_over
          : taken
            ? t.hand_back
            : t.take_over}
      </button>
      <span className="text-od-faint max-w-[48ch] text-[12.5px] text-pretty">
        {taken ? t.taken_note : t.take_over_note}
      </span>
      {error === null ? null : (
        <span className="text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
          {error}
        </span>
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
  const taken = thread.data.handling === "human";

  // The dividers §A6.4 shows, derived from the record before it is drawn. The tracker
  // holds the last business voice seen, so each line knows whether it is a change.
  const rows: { message: ThreadMessage; label: string | null }[] = [];
  let businessSpeaker: string | null = null;
  for (const message of thread.data.messages) {
    rows.push({ message, label: marker(message, businessSpeaker, t) });
    if (!message.is_whisper && message.speaker !== "caller") {
      businessSpeaker = message.speaker;
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[16px_18px]">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
          <span className="text-od-text flex items-center gap-2 text-[16px] font-semibold">
            <ChannelMark id={thread.data.channel} size={14} />
            {who(thread.data, t)}
            {taken && !ended ? (
              <span
                className="rounded-full border px-[9px] py-[2px] text-[11px] font-medium"
                style={{
                  borderColor: "var(--od-amber-border)",
                  background: "var(--od-amber-bg)",
                  color: "var(--od-amber-text)",
                }}
              >
                {t.handling_human}
              </span>
            ) : null}
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
        <>
          <HandlingSwitch
            conversationId={conversationId}
            taken={taken}
            onChanged={thread.reload}
            t={t}
          />
          {taken ? (
            <Composer
              heading={t.reply}
              note={t.reply_note}
              placeholder={t.reply_placeholder}
              sendLabel={t.reply_send}
              sendingLabel={t.reply_sending}
              notAllowed={t.not_allowed_intervene}
              endedNote={t.ended_note}
              submit={(text) => sendReply(conversationId, text)}
              onSent={thread.reload}
            />
          ) : (
            <Composer
              heading={t.whisper}
              note={t.whisper_note}
              placeholder={t.whisper_placeholder}
              sendLabel={t.whisper_send}
              sendingLabel={t.whisper_sending}
              notAllowed={t.not_allowed}
              endedNote={t.ended_note}
              submit={(text) => sendWhisper(conversationId, text)}
              onSent={thread.reload}
            />
          )}
        </>
      )}

      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[16px_18px]">
        <h3 className="text-od-muted-4 m-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.transcript}
        </h3>
        <div className="flex flex-col gap-3">
          {rows.map(({ message, label }) => (
            <div key={message.id} className="flex flex-col gap-3">
              {label === null ? null : <Divider label={label} />}
              <Line message={message} t={t} />
            </div>
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
