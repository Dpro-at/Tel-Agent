"use client";

import Link from "next/link";
import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  ApiError,
  createWorkspace,
  currentUser,
  membersList,
  type Me,
  type Member,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";
import { activeWorkspaceId, setActiveWorkspaceId } from "@/lib/workspace";

import type { NewWorkspaceDictionary } from "./page";

/**
 * Wired to `POST /api/workspaces`.
 *
 * What went, and why: the "Start from" section offered to copy assistants, routing
 * rules and a catalogue — tables that do not exist yet, so a copy would copy
 * nothing. It returns when there is something to copy. "Who gets in" stays: the
 * memberships it copies are real.
 */
export function NewWorkspace({ locale, t }: { locale: Locale; t: NewWorkspaceDictionary }) {
  const me = useResource<Me>(() => currentUser());
  // For the "Everyone in {workspace}" note: how many memberships would be copied.
  // Admin-gated like creation itself, so whoever sees this dialog may ask.
  const members = useResource<Member[]>(() => membersList());

  const [name, setName] = useState("");
  const [includeTeam, setIncludeTeam] = useState(false);
  const [touched, setTouched] = useState(false);
  const [creating, setCreating] = useState(false);
  const [takenName, setTakenName] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const workspaces = me.data?.workspaces ?? [];
  const storedId = me.data === null ? null : activeWorkspaceId();
  const current = workspaces.find((entry) => entry.id === storedId) ?? workspaces[0] ?? null;

  // Who would be copied: everyone except the creator; a pending invitation is not.
  const copied = members.data === null ? null : members.data.filter((entry) => entry.role !== "invited").length - 1;

  const trimmed = name.trim();
  const blocked = trimmed.length < 2;
  const taken = takenName !== null && takenName === trimmed;
  const bad = taken || (touched && blocked);

  async function submit() {
    if (blocked) {
      setTouched(true);
      return;
    }
    setCreating(true);
    setFailure(null);
    try {
      const created = await createWorkspace(trimmed, includeTeam);
      // A workspace is a separate installation in every way that matters, so the
      // browser switches into it and the whole shell reloads there - a full
      // navigation on purpose, since every screen's data changes with the header.
      setActiveWorkspaceId(created.id);
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(`/${locale}/home`);
    } catch (thrown) {
      if (thrown instanceof ApiError && thrown.code === "workspace_name_taken") {
        setTakenName(trimmed);
      } else {
        setFailure(thrown instanceof Error ? thrown.message : String(thrown));
      }
      setCreating(false);
    }
  }

  const accessNote =
    !includeTeam
      ? t.access_me_note
      : copied === null
        ? null
        : interpolate(t.access_team_note, { count: copied });

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="settings" />
      </div>

      <div className="mx-auto max-w-[1400px] p-[26px_28px_90px]">
        <h1 className="text-od-text m-0 text-[24px] font-semibold tracking-[-0.02em]">
          {current?.name ?? ""}
        </h1>
        {/* A workspace is a separate installation, not a folder. */}
        <p className="text-od-muted-4 mt-2 max-w-[64ch] text-pretty">
          {t.behind_intro_before}
          <span className="text-od-text-3">{t.behind_intro_link}</span>
          {t.behind_intro_after}
        </p>
      </div>

      <div
        className="fixed inset-0 z-[60] flex items-start justify-center overflow-auto p-[52px_24px]"
        style={{ background: "var(--od-scrim-3)" }}
      >
        <div
          className="border-od-border-9 bg-od-panel w-full max-w-[560px] overflow-hidden rounded-xl border"
          style={{ boxShadow: "0 30px 80px var(--od-scrim-3)" }}
        >
          <div className="border-od-border flex items-start justify-between gap-4 border-b p-[20px_22px_14px]">
            <div className="min-w-0 max-w-[52ch]">
              <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.title}</h2>
              <p className="text-od-muted-4 mt-[6px] text-[13px] text-pretty">{t.subtitle}</p>
            </div>
            <Link
              href={`/${locale}/home`}
              aria-label={t.close}
              className="border-od-border-2 text-od-muted hover:bg-od-raise hover:text-od-text inline-flex size-[30px] flex-none items-center justify-center rounded-[7px] border text-[14px] hover:no-underline"
            >
              ✕
            </Link>
          </div>

          <div className="p-[20px_22px_4px]">
            <label className="text-od-text-3 block text-[13px] font-medium" htmlFor="workspace-name">
              {t.name_label}
            </label>
            <div className="mt-2 flex items-center gap-[11px]">
              <span
                className="inline-flex size-[38px] flex-none items-center justify-center rounded-[9px] text-[15px] font-semibold"
                style={{
                  border: trimmed
                    ? "1px solid var(--od-violet-border)"
                    : "1px dashed var(--od-stroke-3)",
                  background: trimmed ? "rgba(139,124,255,.14)" : "var(--od-raise-5)",
                  color: trimmed ? "var(--od-violet-3)" : "var(--od-faint)",
                }}
              >
                {trimmed ? trimmed.slice(0, 1).toUpperCase() : "+"}
              </span>
              <input
                id="workspace-name"
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t.name_placeholder}
                className="bg-od-panel-deep-2 text-od-text min-w-0 flex-[1_1_auto] rounded-lg border p-[10px_13px] text-[14.5px] outline-none"
                style={{ borderColor: bad ? "var(--od-red-border)" : "var(--od-border-2)" }}
              />
            </div>
            <p
              className="mt-2 text-[12.5px]"
              style={{ color: bad ? "var(--od-red-text-2)" : "var(--od-faint)" }}
            >
              {taken ? t.name_help_taken : touched && blocked ? t.name_help_blank : t.name_help}
            </p>

            <div className="mt-5">
              <div className="text-od-text-3 text-[13px] font-medium">{t.access_label}</div>
              <div className="mt-[9px] flex flex-wrap gap-2">
                {(
                  [
                    { team: false, label: t.access_me },
                    {
                      team: true,
                      label: interpolate(t.access_team, { workspace: current?.name ?? "…" }),
                    },
                  ] as const
                ).map((entry) => {
                  const on = includeTeam === entry.team;
                  return (
                    <button
                      key={String(entry.team)}
                      type="button"
                      onClick={() => setIncludeTeam(entry.team)}
                      className="cursor-pointer rounded-full border p-[7px_13px] text-start text-[13px]"
                      style={{
                        borderColor: on ? "var(--od-violet-border)" : "var(--od-border-2)",
                        background: on ? "rgba(139,124,255,.14)" : "transparent",
                        color: on ? "var(--od-violet-3)" : "var(--od-muted-4)",
                        fontWeight: on ? 500 : 400,
                      }}
                    >
                      {entry.label}
                    </button>
                  );
                })}
              </div>
              {accessNote ? (
                <p className="text-od-faint mt-[9px] text-[12.5px] text-pretty">{accessNote}</p>
              ) : null}
            </div>

            {taken ? (
              <div className="border-od-red-border bg-od-red-bg mt-[18px] flex items-start gap-[11px] rounded-[9px] border p-[13px_15px]">
                <span className="mt-px flex-none text-[color:var(--od-red-text)]">!</span>
                <div className="min-w-0 text-[13px] text-pretty text-[color:var(--od-red-text-2)]">
                  {t.taken_before}
                  <span className="text-[color:var(--od-red-text)]">{takenName}</span>
                  {t.taken_after}
                </div>
              </div>
            ) : null}

            {failure ? (
              <p className="mt-[18px] text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
                {failure}
              </p>
            ) : null}

            <div className="border-od-border text-od-faint mt-[18px] border-t pt-[15px] text-[12.5px] text-pretty">
              {t.billing}
            </div>
          </div>

          <div className="border-od-border bg-od-panel-deep-2 mt-3 flex flex-wrap items-center justify-end gap-[10px] border-t p-[16px_22px]">
            {creating ? (
              <span className="text-od-muted-4 me-auto inline-flex items-center gap-[9px] text-[12.5px]">
                <span
                  className="size-[13px] rounded-full border-2 border-[color:var(--od-stroke-3)]"
                  style={{
                    borderTopColor: "var(--od-violet)",
                    animation: "od-spin .7s linear infinite",
                  }}
                />
                <span>{t.creating}</span>
              </span>
            ) : null}
            <Link
              href={`/${locale}/home`}
              className="border-od-border-2 text-od-muted hover:bg-od-raise hover:text-od-text-2 rounded-[7px] border p-[9px_15px] text-[13.5px] hover:no-underline"
            >
              {t.cancel}
            </Link>
            <button
              type="button"
              disabled={creating}
              onClick={() => void submit()}
              className="rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold"
              style={{
                borderColor:
                  blocked || creating ? "var(--od-border-2)" : "var(--od-violet-border)",
                background: blocked || creating ? "var(--od-raise-5)" : "var(--od-violet)",
                color: blocked || creating ? "var(--od-faint)" : "#12101d",
                cursor: creating ? "default" : blocked ? "not-allowed" : "pointer",
                opacity: creating ? 0.8 : 1,
              }}
            >
              {creating ? t.submit_busy : t.submit}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
