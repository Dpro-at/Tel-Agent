"use client";

import type { Route } from "next";
import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";

import ar from "../../../locales/ar/shell.json";
import de from "../../../locales/de/shell.json";
import en from "../../../locales/en/shell.json";

import { NavIcon } from "@/components/shell/icons";
import { IncomingCall } from "@/components/shell/incoming-call";
import { currentUser, signOut, type Me } from "@/lib/api";
import { interpolate, pickDictionary } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";
import { activeWorkspaceId, setActiveWorkspaceId } from "@/lib/workspace";

/**
 * The theme lives on `<html data-od-theme>`, written by the inline script in the
 * locale layout before first paint so the page never flashes the wrong palette.
 * This subscription reads that attribute instead of keeping a second copy in
 * React state: the server always renders the dark icon, and the client swaps to
 * whatever the document already says once it hydrates.
 */
const THEME_EVENT = "od-theme-change";

function subscribeTheme(onChange: () => void) {
  addEventListener(THEME_EVENT, onChange);
  return () => removeEventListener(THEME_EVENT, onChange);
}

function readTheme(): "dark" | "light" {
  return document.documentElement.getAttribute("data-od-theme") === "light" ? "light" : "dark";
}

/** What the server renders, and what hydration matches against. */
function darkTheme(): "dark" {
  return "dark";
}

/**
 * The shell appears on every screen, so it carries its own dictionary rather than
 * being handed one by each of the twenty-six pages. It is a few dozen short labels.
 */
type ShellDictionary = typeof en;
const DICTIONARIES = { en, de: de as ShellDictionary, ar: ar as ShellDictionary };

type LabelKey = keyof ShellDictionary;

/**
 * A row is labelled either by a dictionary key or by a literal product name -
 * WhatsApp is WhatsApp in all three languages, so it is never a translation key.
 */
type Kid = {
  id?: string;
  label?: LabelKey;
  name?: string;
  href?: string;
  count?: number;
  live?: boolean;
  tone: Tone;
};
type Item = { id: string; label: LabelKey; href: string; icon: string; kids?: Kid[] };
type Tone = "violet" | "green" | "grey";

const TONES: Record<Tone, string> = {
  violet: "var(--od-violet)",
  green: "var(--od-green)",
  grey: "var(--od-stroke-5)",
};

/** Channel names - WhatsApp, Telegram - are product names and are never translated. */
const CHANNELS: { id: string; name: string; count: number; tone: Tone }[] = [
  { id: "whatsapp", name: "WhatsApp", count: 3, tone: "green" },
  { id: "telegram", name: "Telegram", count: 1, tone: "grey" },
  { id: "sms", name: "SMS", count: 1, tone: "grey" },
  { id: "web chat", name: "Web chat", count: 1, tone: "grey" },
];

const NAV: { label: LabelKey; items: Item[] }[] = [
  {
    label: "group_overview",
    items: [
      { id: "home", label: "nav_home", href: "/home", icon: "home" },
      {
        id: "calls",
        label: "nav_calls",
        href: "/calls",
        icon: "phone",
        kids: [
          { id: "live", label: "nav_live", href: "/live", live: true, tone: "violet" },
          { id: "archive", label: "nav_archive", href: "/calls", tone: "grey" },
          { id: "campaigns", label: "nav_campaigns", href: "/campaigns", tone: "violet" },
          { id: "consent", label: "nav_consent", href: "/consent", tone: "grey" },
        ],
      },
      {
        id: "conversations",
        label: "nav_channels",
        href: "/conversations",
        icon: "message",
        kids: CHANNELS.map((channel) => ({
          id: channel.id,
          name: channel.name,
          count: channel.count,
          tone: channel.tone,
        })),
      },
      { id: "notifications", label: "nav_notifications", href: "/notifications", icon: "bell" },
      { id: "calendar", label: "nav_calendar", href: "/calendar", icon: "calendar" },
      { id: "contacts", label: "nav_contacts", href: "/contacts", icon: "users" },
      { id: "assistants", label: "nav_assistants", href: "/assistants", icon: "bot" },
    ],
  },
];

/** The note under each workspace in the switcher: the reader's role there. */
const ROLE_KEY: Record<string, LabelKey> = {
  owner: "role_owner",
  admin: "role_admin",
  reception: "role_reception",
  viewer: "role_viewer",
  invited: "role_invited",
};

export function Sidebar({
  locale,
  active,
  liveCalls = 3,
  incomingCall = false,
}: {
  locale: Locale;
  active: string;
  liveCalls?: number;
  incomingCall?: boolean;
}) {
  const t = pickDictionary<ShellDictionary>(locale, DICTIONARIES);
  const theme = useSyncExternalStore(subscribeTheme, readTheme, darkTheme);
  const [open, setOpen] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  // Who is signed in and which workspaces they belong to — the switcher's truth.
  // The shell renders on every screen, so this is one request per page, answered
  // from the same session cookie every other call already carries.
  const me = useResource<Me>(() => currentUser());
  const workspaces = me.data?.workspaces ?? [];
  const storedId = me.data === null ? null : activeWorkspaceId();
  const current = workspaces.find((entry) => entry.id === storedId) ?? workspaces[0] ?? null;

  // A stored id whose membership is gone — removed from the workspace, or the
  // workspace deleted — must not keep every request pinned to a 403.
  useEffect(() => {
    if (me.data !== null && storedId !== null && current !== null && current.id !== storedId) {
      setActiveWorkspaceId(null);
    }
  }, [me.data, storedId, current]);

  function switchWorkspace(id: number) {
    setActiveWorkspaceId(id);
    // A workspace is a separate installation in every way that matters, so the
    // whole page reloads into it rather than patching state screen by screen.
    window.location.reload();
  }

  async function signOutAndLeave() {
    try {
      await signOut();
    } catch {
      // The server may be unreachable; the sign-in screen is still the right place
      // to land, and the cookie the server holds expires on its own.
    }
    // A full-document navigation on purpose: signing out must drop every piece of
    // in-memory state the dashboard holds, which a client-side route change keeps.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/${locale}/login`);
  }

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    const root = document.documentElement;
    if (next === "light") root.setAttribute("data-od-theme", "light");
    else root.removeAttribute("data-od-theme");
    try {
      localStorage.setItem("od-theme", next);
    } catch {
      // A browser with storage disabled still switches, it just does not remember.
    }
    dispatchEvent(new Event(THEME_EVENT));
  }

  /**
   * Keeps the path a literal type through the locale prefix, so `typedRoutes`
   * still checks every link here rather than seeing a plain string.
   */
  const href = <P extends string>(path: P): `/${Locale}${P}` => `/${locale}${path}`;

  return (
    <>
      <IncomingCall locale={locale} enabled={incomingCall} />

      <div className="bg-od-canvas-2 text-od-text border-od-border flex h-full flex-col gap-5 border-e p-[18px_12px] text-[14px] leading-[1.45]">
        <div className="flex items-center justify-between gap-2 p-[4px_11px]">
          <Link href={href("/home")} className="text-od-text flex items-baseline gap-2 hover:no-underline">
            <span className="font-semibold tracking-[-0.01em]">Tel-Agent</span>
            <span className="mono ltr-data text-od-faint text-[11px]">v1.4.2</span>
          </Link>
          <button
            type="button"
            onClick={toggleTheme}
            title={theme === "dark" ? t.theme_to_light : t.theme_to_dark}
            aria-label={theme === "dark" ? t.theme_to_light : t.theme_to_dark}
            className="border-od-border-2 bg-od-panel text-od-muted-4 hover:bg-od-raise hover:text-od-text inline-flex size-[26px] flex-none cursor-pointer items-center justify-center rounded-[7px] border text-[13px] leading-none"
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>

        <nav className="flex min-h-0 flex-[1_1_auto] flex-col gap-[18px] overflow-auto">
          {NAV.map((group) => (
            <div key={group.label} className="flex flex-col gap-[2px]">
              <div className="p-[0_11px_6px] text-[10.5px] tracking-[.1em] uppercase text-[color:var(--od-faint-5)]">
                {t[group.label]}
              </div>
              {group.items.map((item) => {
                const on = item.id === active;
                const kidId = (kid: Kid) => kid.id ?? (kid.name ?? kid.label ?? "").toLowerCase();
                const kidActive = (item.kids ?? []).some((kid) => kidId(kid) === active);
                const expanded =
                  open === item.id || (open === null && (on || kidActive) && Boolean(item.kids));

                return (
                  <div key={item.id}>
                    <div className="flex items-center gap-[2px]">
                      <Link
                        href={href(item.href)}
                        className={`hover:bg-od-raise hover:text-od-text-2 flex min-w-0 flex-[1_1_auto] items-center gap-[10px] rounded-[7px] p-[8px_11px] hover:no-underline ${
                          on ? "bg-[var(--od-raise-7)] text-od-text font-medium" : "text-od-muted-4"
                        }`}
                      >
                        <span className="inline-flex size-[17px] flex-none items-center justify-center">
                          <NavIcon
                            name={item.icon}
                            color={on ? "var(--od-text)" : "var(--od-faint-2)"}
                          />
                        </span>
                        <span>{t[item.label]}</span>
                      </Link>
                      {item.kids ? (
                        <button
                          type="button"
                          onClick={() => setOpen(open === item.id ? "__none" : item.id)}
                          aria-label={t.expand_children}
                          className="text-od-faint-2 hover:bg-od-raise hover:text-od-text-2 inline-flex h-[30px] w-6 flex-none cursor-pointer items-center justify-center rounded-md border-none bg-transparent text-[12px] leading-none"
                        >
                          {expanded ? "⌄" : "›"}
                        </button>
                      ) : null}
                    </div>

                    {item.kids && expanded ? (
                      <div className="my-[3px] flex flex-col gap-px ps-[21px]">
                        {item.kids.map((kid) => {
                          const id = kidId(kid);
                          const count = kid.live ? liveCalls || null : (kid.count ?? null);
                          return (
                            <Link
                              key={id}
                              // A fragment is not part of a route, so only that half is cast.
                              href={
                                kid.href
                                  ? href(kid.href)
                                  : (`${href(item.href)}#${id.replace(" ", "-")}` as Route)
                              }
                              className={`flex items-center gap-[9px] rounded-md p-[6px_10px] text-[13px] hover:no-underline ${
                                id === active
                                  ? "bg-[var(--od-raise-7)] text-od-text font-medium"
                                  : "text-od-muted-4 hover:bg-od-raise hover:text-od-text-2"
                              }`}
                            >
                              <span
                                className="size-[6px] flex-none rounded-full"
                                style={{ background: TONES[kid.tone] }}
                              />
                              <span className="min-w-0">
                                {kid.name ?? (kid.label ? t[kid.label] : null)}
                              </span>
                              {count ? (
                                <span className="mono ltr-data text-od-faint-2 ms-auto text-[10.5px]">
                                  {count}
                                </span>
                              ) : null}
                            </Link>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="flex flex-none flex-col gap-3">
          {liveCalls > 0 && active !== "live" ? (
            <Link
              href={href("/live")}
              className="text-od-text-2 hover:bg-od-raise flex items-center gap-[9px] rounded-[9px] border border-[color:var(--od-violet-border)] bg-[var(--od-canvas-violet)] p-[9px_11px] text-[13px] font-medium hover:no-underline"
            >
              <span
                className="size-2 flex-none rounded-full bg-[color:var(--od-violet)]"
                style={{ animation: "od-ring-violet 1.8s ease-out infinite" }}
              />
              <span className="min-w-0">
                {liveCalls === 1
                  ? t.on_the_line_one
                  : interpolate(t.on_the_line_many, { count: liveCalls })}
              </span>
            </Link>
          ) : null}

          <div className="border-od-line relative border-t">
            {menuOpen ? (
              <div
                className="bg-od-panel border-od-border-9 absolute bottom-[calc(100%+8px)] start-0 z-[90] w-[246px] max-w-[calc(100vw-24px)] rounded-[11px] border p-[6px]"
                style={{ boxShadow: "0 18px 44px var(--od-scrim-3)" }}
              >
                <div className="p-[8px_10px_6px] text-[10.5px] tracking-[.1em] uppercase text-[color:var(--od-faint-5)]">
                  {t.workspace_heading}
                </div>
                {workspaces.map((entry) => {
                  const active_ = current !== null && entry.id === current.id;
                  const roleKey = ROLE_KEY[entry.role];
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() => {
                        setMenuOpen(false);
                        if (!active_) switchWorkspace(entry.id);
                      }}
                      className="hover:bg-od-raise flex w-full cursor-pointer items-center gap-[9px] rounded-lg border-none bg-transparent p-[7px_10px]"
                    >
                      <span
                        className="inline-flex size-6 flex-none items-center justify-center rounded-[7px] border text-[11.5px] font-semibold"
                        style={{
                          borderColor: active_ ? "var(--od-violet-border)" : "var(--od-border-9)",
                          background: active_ ? "rgba(139,124,255,.14)" : "var(--od-raise-5)",
                          color: active_ ? "var(--od-violet-3)" : "var(--od-muted-2)",
                        }}
                      >
                        {entry.name.slice(0, 1).toUpperCase()}
                      </span>
                      <span className="min-w-0 flex-[1_1_auto] text-start">
                        <span className="text-od-text block text-[13px] font-medium">{entry.name}</span>
                        {roleKey ? (
                          <span className="text-od-faint mt-px block text-[11.5px]">
                            {t[roleKey]}
                          </span>
                        ) : null}
                      </span>
                      {active_ ? (
                        <span className="flex-none text-[12px] text-[color:var(--od-violet-2)]">✓</span>
                      ) : null}
                    </button>
                  );
                })}
                <Link
                  href={href("/workspaces/new")}
                  className="text-od-muted-4 hover:bg-od-raise hover:text-od-text-2 mt-[3px] flex w-full items-center gap-[9px] rounded-lg p-[8px_10px] text-[13px] hover:no-underline"
                >
                  <span className="border-od-stroke-3 inline-flex size-6 flex-none items-center justify-center rounded-[7px] border border-dashed text-[13px] leading-none">
                    +
                  </span>
                  <span>{t.workspace_new}</span>
                </Link>
                <div className="bg-od-border m-[6px_4px] h-px" />
                <Link
                  href={href("/settings")}
                  className="text-od-text-3 hover:bg-od-raise hover:text-od-text flex items-center gap-[9px] rounded-lg p-[8px_10px] text-[13px] hover:no-underline"
                >
                  {t.settings}
                </Link>
                <div className="bg-od-border m-[6px_4px] h-px" />
                <button
                  type="button"
                  onClick={() => void signOutAndLeave()}
                  className="text-od-muted-4 hover:bg-od-raise hover:text-od-text-2 flex w-full cursor-pointer items-center gap-[9px] rounded-lg border-none bg-transparent p-[8px_10px] text-start text-[13px]"
                >
                  {t.sign_out}
                </button>
              </div>
            ) : null}

            <button
              type="button"
              onClick={() => setMenuOpen((value) => !value)}
              aria-label={t.account_menu}
              className={`flex w-full cursor-pointer items-center gap-[9px] rounded-[7px] border-none p-[8px_11px] ${
                menuOpen ? "bg-od-raise" : "bg-transparent"
              }`}
            >
              <span className="border-od-border-9 text-od-text-2 inline-flex size-[26px] flex-none items-center justify-center rounded-full border bg-[var(--od-raise-5)] text-[11.5px] font-semibold">
                {me.data?.username.slice(0, 1).toUpperCase() ?? "·"}
              </span>
              <span className="min-w-0 flex-[1_1_auto] text-start">
                <span className="text-od-text-2 block text-[13px]">
                  {me.data?.username ?? "…"}
                </span>
                {current ? (
                  <span className="text-od-faint block text-[11.5px]">{current.name}</span>
                ) : null}
              </span>
              <span className="text-od-faint-2 flex-none text-[10px]">{menuOpen ? "⌄" : "›"}</span>
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
