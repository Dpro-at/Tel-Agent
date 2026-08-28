"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { LiveSettings, type FieldCopy } from "@/components/settings/live-settings";
import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import {
  API_KEYS,
  HOST_FIELDS,
  INVITE_ROLES,
  MEMBERS,
  OUR_TOOLS,
  PAGE_LINKS,
  ROLE_COLUMNS,
  ROLE_LABEL,
  ROLE_MATRIX,
  SECTIONS,
  TABS,
  WEBHOOKS,
  type Control,
  type Field,
} from "@/lib/settings/data";

import type { SettingsDictionary } from "./page";

const ROLE_GRID = "minmax(200px, 1.4fr) repeat(4, minmax(96px, 1fr))";

function Switch({ on }: { on: boolean }) {
  return (
    <span
      className="inline-flex h-[22px] w-10 items-center rounded-full border p-[2px]"
      style={{
        borderColor: on ? "var(--od-violet)" : "var(--od-border-7)",
        background: on ? "var(--od-violet)" : "var(--od-raise)",
        justifyContent: on ? "flex-end" : "flex-start",
      }}
    >
      <span
        className="size-4 rounded-full"
        style={{ background: on ? "#fff" : "var(--od-stroke-5)" }}
      />
    </span>
  );
}

function ControlView({ t, control }: { t: SettingsDictionary; control: Control }) {
  if (control.kind === "switch") return <Switch on={control.on} />;

  if (control.kind === "select") {
    return (
      <div className="border-od-border-6 bg-od-canvas-2 text-od-text-2 flex items-center justify-between gap-[10px] rounded-[7px] border p-[9px_12px]">
        {/* An address or a language's own name keeps its direction; a phrase follows the page. */}
        {control.value ? (
          <span>{t[control.value]}</span>
        ) : (
          <span dir="ltr" className="ltr-data text-start">
            {control.valueText}
          </span>
        )}
        <span className="text-od-faint-2">▾</span>
      </div>
    );
  }

  return (
    <div
      dir={control.mono ? "ltr" : undefined}
      className={`bg-od-canvas-2 rounded-[7px] border p-[9px_12px] [overflow-wrap:anywhere] ${
        control.mono ? "mono ltr-data text-[13px]" : "text-[14px]"
      }`}
      style={{
        borderColor: control.invalid ? "var(--od-red-border-2)" : "var(--od-border-6)",
        color: "var(--od-text-2)",
      }}
    >
      {control.value ? t[control.value] : control.valueText}
    </div>
  );
}

function FieldRow({
  t,
  field,
  first = false,
}: {
  t: SettingsDictionary;
  field: Field;
  first?: boolean;
}) {
  return (
    <div
      className={`flex flex-wrap items-start justify-between gap-x-6 gap-y-3 p-[14px_18px] ${
        first ? "" : "border-t border-[color:var(--od-raise-6)]"
      }`}
    >
      <div className="min-w-[200px] flex-[1_1_240px]">
        <div className="text-od-text-3 font-medium text-pretty">{t[field.label]}</div>
        {field.help ? (
          <div className="text-od-muted-5 mt-1 max-w-[52ch] text-[12.5px] text-pretty">
            {t[field.help]}
          </div>
        ) : null}
      </div>
      <div className="min-w-[min(100%,240px)] flex-[0_1_300px]">
        <ControlView t={t} control={field.control} />
      </div>
    </div>
  );
}

function SectionHead({ title, note }: { title: string; note?: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px] p-[18px_18px_4px]">
      <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
        {title}
      </h3>
      {note ? <span className="text-od-faint text-[12.5px] text-pretty">{note}</span> : null}
    </div>
  );
}

/** The label bundle every live panel needs, assembled once from the dictionary. */
function liveLabels(t: SettingsDictionary) {
  return {
    save: t.live_save,
    saving: t.live_saving,
    saved: t.live_saved,
    unchanged: t.live_unchanged,
    loading: t.live_loading,
    failed: t.live_failed,
    retry: t.live_retry,
    secretKept: t.live_secret_kept,
  };
}

/**
 * The mail server, read from and written to the settings store.
 *
 * These six keys are the ones the forgot-password screen depends on — its `no_mail`
 * state is exactly "smtp.host is empty" — so they are the first settings that had to
 * stop being a drawing.
 */
function mailFields(t: SettingsDictionary): FieldCopy[] {
  return [
    { key: "smtp.host", label: t.f_smtp_host, help: t.f_smtp_host_help },
    { key: "smtp.port", label: t.f_smtp_port, help: t.f_smtp_port_help },
    { key: "smtp.username", label: t.f_smtp_user },
    { key: "smtp.password", label: t.f_smtp_pass, help: t.f_smtp_pass_help },
    { key: "smtp.from", label: t.f_send_as, help: t.f_send_as_help },
    { key: "smtp.use_tls", label: t.f_smtp_tls, help: t.f_smtp_tls_help },
    { key: "smtp.use_ssl", label: t.f_smtp_ssl, help: t.f_smtp_ssl_help },
  ];
}

function backupFields(t: SettingsDictionary): FieldCopy[] {
  return [
    { key: "backup.target_path", label: t.f_backup_path, help: t.f_backup_path_help },
    {
      key: "backup.include_recordings",
      label: t.f_backup_recordings,
      help: t.f_backup_recordings_help,
    },
  ];
}

export function Settings({ locale, t }: { locale: Locale; t: SettingsDictionary }) {
  const router = useRouter();
  const [state, setState] = useState<ScreenState>("default");
  const [tab, setTab] = useState("general");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [memberMenu, setMemberMenu] = useState<string | null>(null);

  const offline = state === "offline";
  const empty = state === "empty";
  const section = SECTIONS[tab];

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="settings" />
      </div>

      <StatePreview state={state} onChange={setState} />

      {offline ? (
        <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-[14px]">
          <span
            className="size-[10px] flex-none rounded-full bg-[#F0605E]"
            style={{ animation: "od-ring 1.6s ease-out infinite" }}
          />
          <div className="min-w-[240px] flex-[1_1_340px]">
            <div className="text-[15px] font-semibold text-[color:var(--od-red-text)]">
              {t.offline_title}
            </div>
            <div className="mt-[3px] text-[color:var(--od-red-text-2)]">
              {t.offline_body_before}
              <span className="mono">403 Forbidden</span>
              {t.offline_body_after}
            </div>
          </div>
          <button
            type="button"
            className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[8px_14px] font-medium text-[color:var(--od-red-text-3)]"
          >
            {t.offline_test}
          </button>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1240px] p-[26px_28px_80px]">
        {state === "error" ? (
          <ReadOnlyConfig t={t} />
        ) : (
          <div>
            <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">{t.title}</h1>

            <div className="mt-[22px] flex flex-wrap items-start gap-6">
              <nav className="flex max-w-[300px] min-w-[min(100%,210px)] flex-[1_1_220px] flex-col gap-[2px]">
                {TABS.map((entry) => {
                  const on = tab === entry.id;
                  return (
                    <button
                      key={entry.id}
                      type="button"
                      onClick={() =>
                        entry.href ? router.push(`/${locale}${entry.href}`) : setTab(entry.id)
                      }
                      className="flex cursor-pointer items-center justify-between gap-[10px] rounded-[7px] p-[10px_13px] text-start text-[14px] whitespace-normal"
                      style={{
                        borderWidth: 1,
                        borderStyle: "solid",
                        borderColor: on ? "var(--od-border-9)" : "transparent",
                        background: on ? "var(--od-raise-5)" : "transparent",
                        color: on ? "var(--od-text)" : "var(--od-muted-4)",
                      }}
                    >
                      <span>{t[entry.label]}</span>
                    </button>
                  );
                })}

                {PAGE_LINKS.map((entry) => (
                  <Link
                    key={entry.id}
                    href={`/${locale}${entry.href}`}
                    className="text-od-muted-4 hover:bg-od-raise hover:text-od-text flex items-center gap-[10px] rounded-[7px] border border-transparent p-[10px_13px] text-[14px] hover:no-underline"
                  >
                    {t[entry.label]}
                  </Link>
                ))}
              </nav>

              <div className="flex min-w-[min(100%,460px)] flex-[3_1_520px] flex-col gap-4">
                {state === "loading" ? (
                  <div className="flex flex-col gap-4">
                    {[150, 240, 120].map((height) => (
                      <div
                        key={height}
                        className="border-od-raise-12 rounded-[10px] border"
                        style={{
                          height,
                          background:
                            "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
                          backgroundSize: "420px 100%",
                          animation: "od-shimmer 1.4s linear infinite",
                        }}
                      />
                    ))}
                  </div>
                ) : empty ? (
                  <div className="border-od-border-6 bg-od-panel-deep-2 rounded-[10px] border border-dashed p-[40px_28px]">
                    <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
                    <p className="text-od-muted mt-[10px] max-w-[58ch] text-pretty">{t.empty_body}</p>
                    <Link
                      href={`/${locale}/numbers`}
                      className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-[18px] inline-block rounded-md border p-[9px_16px] font-medium hover:no-underline"
                    >
                      {t.empty_go}
                    </Link>
                  </div>
                ) : (
                  <div className="flex flex-col gap-4">
                    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
                      <h2 className="text-od-text m-0 text-[16px] font-semibold">{t[section.title]}</h2>
                      <p className="text-od-muted-4 mt-[6px] max-w-[64ch] text-pretty">
                        {t[section.body]}
                      </p>
                    </div>

                    {tab === "profile" ? <ProfilePanels t={t} /> : null}
                    {tab === "users" ? (
                      <UsersPanels
                        t={t}
                        onInvite={() => setInviteOpen(true)}
                        memberMenu={memberMenu}
                        onMemberMenu={setMemberMenu}
                      />
                    ) : null}
                    {tab === "api" ? <ApiPanels t={t} /> : null}
                    {tab === "mcp" ? <McpPanels locale={locale} t={t} /> : null}
                    {tab === "advanced" ? (
                      <>
                        <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
                          <SectionHead
                            title={t.backup_section_title}
                            note={t.backup_section_note}
                          />
                          <LiveSettings fields={backupFields(t)} labels={liveLabels(t)} />
                        </div>
                        <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
                          <SectionHead title={t.host_title} note={t.host_note} />
                          {HOST_FIELDS.map((field) => (
                            <FieldRow key={field.id} t={t} field={field} />
                          ))}
                        </div>
                      </>
                    ) : null}
                    {tab === "notifications" ? (
                      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
                        <SectionHead title={t.smtp_title} note={t.live_note} />
                        <LiveSettings fields={mailFields(t)} labels={liveLabels(t)} />
                      </div>
                    ) : null}

                    {section.fields.length > 0 ? (
                      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
                        {section.fields.map((field) => (
                          <div key={field.id} className="border-b border-[color:var(--od-raise-6)]">
                            <FieldRow t={t} field={field} first />
                          </div>
                        ))}
                      </div>
                    ) : null}

                    <div className="flex flex-wrap items-center justify-end gap-[10px]">
                      <button
                        type="button"
                        className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_15px]"
                      >
                        {t.discard}
                      </button>
                      <button
                        type="button"
                        className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 inline-flex cursor-pointer items-center gap-[9px] rounded-md border p-[9px_16px] font-medium whitespace-nowrap"
                      >
                        {t.save_changes}{" "}
                        <span className="mono ltr-data text-od-faint text-[11.5px]">⌘↵</span>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {inviteOpen ? <InviteDialog t={t} onClose={() => setInviteOpen(false)} /> : null}
    </div>
  );
}

function ProfilePanels({ t }: { t: SettingsDictionary }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="border-od-line bg-od-panel-deep-3 flex flex-wrap items-center gap-[18px] rounded-[10px] border p-[18px]">
        <span className="border-od-border-9 text-od-text-3 inline-flex size-16 flex-none items-center justify-center rounded-full border bg-[var(--od-raise-5)] text-[22px] font-semibold">
          M
        </span>
        <div className="min-w-[200px] flex-[1_1_240px]">
          <div className="text-od-text text-[16px] font-semibold">Mohamed</div>
          <div className="text-od-muted-5 mt-[3px] text-[13px]">
            {interpolate(t.p_signed_in, { role: t.role_admin })}
          </div>
        </div>
        <div className="flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium whitespace-nowrap"
          >
            {t.p_upload}
          </button>
          <button
            type="button"
            className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[8px_14px] text-[13px] whitespace-nowrap hover:bg-[var(--od-raise-4)]"
          >
            {t.p_remove}
          </button>
        </div>
      </div>

      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <h3 className="text-od-muted-4 mt-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.p_security}
        </h3>

        <div className="border-od-border flex flex-wrap items-center gap-x-[18px] gap-y-3 border-b py-[13px]">
          <div className="min-w-[220px] flex-[1_1_260px]">
            <div className="text-od-text-3 font-medium">{t.p_password}</div>
            <div className="text-od-muted-5 mt-[3px] text-[13px]">{t.p_password_last}</div>
          </div>
          <button
            type="button"
            className="border-od-border-7 text-od-text-3 ms-auto cursor-pointer rounded-md border bg-transparent p-[8px_14px] text-[13px] whitespace-nowrap hover:bg-[var(--od-raise-4)]"
          >
            {t.p_change_password}
          </button>
        </div>

        <div className="border-od-border flex flex-wrap items-center gap-x-[18px] gap-y-3 border-b py-[13px]">
          <div className="min-w-[220px] flex-[1_1_260px]">
            <div className="flex flex-wrap items-center gap-[9px]">
              <span className="text-od-text-3 font-medium">{t.p_2fa}</span>
              <span className="border-od-amber-border bg-od-amber-bg rounded-md border p-[2px_9px] text-[12px] font-medium text-[color:var(--od-amber-text)]">
                {t.p_off}
              </span>
            </div>
            <div className="text-od-muted-5 mt-[3px] text-[13px] text-pretty">
              {t.p_2fa_note}
            </div>
          </div>
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 ms-auto cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium whitespace-nowrap"
          >
            {t.p_setup}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-x-[18px] gap-y-3 py-[13px]">
          <div className="min-w-[220px] flex-[1_1_260px]">
            <div className="text-od-text-3 font-medium">{t.p_devices}</div>
            <div className="text-od-muted-5 mt-[3px] text-[13px]">{t.p_devices_note}</div>
          </div>
          <button
            type="button"
            className="ms-auto cursor-pointer border-none bg-transparent p-0 text-[13px] text-[color:var(--od-red-text-4)] hover:underline"
          >
            {t.p_signout_all}
          </button>
        </div>
      </div>
    </div>
  );
}

function UsersPanels({
  t,
  onInvite,
  memberMenu,
  onMemberMenu,
}: {
  t: SettingsDictionary;
  onInvite: () => void;
  memberMenu: string | null;
  onMemberMenu: (email: string | null) => void;
}) {
  return (
    <>
      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-[10px]">
          <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.u_team}
          </h3>
          <button
            type="button"
            onClick={onInvite}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium whitespace-nowrap"
          >
            {t.u_invite}
          </button>
        </div>

        <div className="mt-[10px] flex flex-col">
          {MEMBERS.map((member) => {
            const invited = member.role === "invited";
            const active = member.hasPhone && !invited;
            return (
              <div
                key={member.email}
                className="border-od-border flex flex-wrap items-center gap-x-[18px] gap-y-3 border-b py-[13px]"
              >
                <span className="border-od-border-9 text-od-text-3 inline-flex size-8 flex-none items-center justify-center rounded-full border bg-[var(--od-raise-5)] text-[12.5px] font-semibold">
                  {member.name.charAt(0)}
                </span>
                <div className="min-w-[180px] flex-[1_1_220px]">
                  <div className="flex flex-wrap items-center gap-[9px]">
                    <span className="text-od-text font-medium">{member.name}</span>
                    <span
                      className="rounded-md border p-[2px_9px] text-[12px] font-medium"
                      style={{
                        borderColor: invited ? "var(--od-amber-border)" : "var(--od-border-7)",
                        background: invited ? "var(--od-amber-bg)" : "var(--od-raise-5)",
                        color: invited ? "var(--od-amber-text)" : "var(--od-muted-4)",
                      }}
                    >
                      {t[ROLE_LABEL[member.role]]}
                    </span>
                    {/* A role alone does not let someone answer — a phone must be registered. */}
                    <span
                      className="rounded-[5px] border p-[1px_8px] text-[11px] font-semibold whitespace-nowrap"
                      style={{
                        borderColor: active ? "var(--od-green-border)" : "var(--od-border-2)",
                        background: active ? "rgba(63,185,132,.10)" : "transparent",
                        color: active ? "var(--od-green-text)" : "var(--od-faint)",
                      }}
                    >
                      {invited ? t.u_not_active : member.hasPhone ? t.u_can_answer : t.u_no_phone}
                    </span>
                  </div>
                  <div
                    dir="ltr"
                    className="text-od-muted-5 mt-[3px] text-[12.5px] [overflow-wrap:anywhere]"
                  >
                    {member.email}
                  </div>
                  <div className="text-od-faint mt-[5px] flex flex-wrap gap-x-[10px] gap-y-1 text-[12px]">
                    <span className="text-pretty">{t[member.access]}</span>
                    <span className="text-[color:var(--od-faint-5)]">·</span>
                    <span className="text-pretty">{t[member.elsewhere]}</span>
                  </div>
                </div>

                <div className="relative ms-auto">
                  <button
                    type="button"
                    onClick={() => onMemberMenu(memberMenu === member.email ? null : member.email)}
                    aria-label={t.u_more}
                    className="text-od-muted-4 hover:text-od-text-3 inline-flex size-[30px] cursor-pointer items-center justify-center rounded-[7px] border border-transparent bg-transparent text-[17px] leading-none hover:bg-[var(--od-raise-5)]"
                  >
                    ⋯
                  </button>
                  {memberMenu === member.email ? (
                    <div
                      className="border-od-border-9 bg-od-panel absolute top-[34px] end-0 z-50 flex w-[208px] flex-col gap-px rounded-[9px] border p-[5px]"
                      style={{ boxShadow: "0 12px 28px var(--od-scrim-4)" }}
                    >
                      {[
                        { id: "perms", label: t.u_change_perms, tone: "" },
                        {
                          id: "reset",
                          label: invited ? t.u_resend : t.u_reset_pw,
                          tone: "",
                        },
                        { id: "activity", label: t.u_activity, tone: "" },
                        {
                          id: "remove",
                          label: invited ? t.u_cancel_invite : t.u_remove,
                          tone: member.removable ? "danger" : "off",
                        },
                      ].map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          className="rounded-md border-none bg-transparent p-[8px_10px] text-start text-[13.5px] hover:bg-[var(--od-raise-5)]"
                          style={{
                            cursor: item.tone === "off" ? "not-allowed" : "pointer",
                            color:
                              item.tone === "danger"
                                ? "var(--od-red-text-4)"
                                : item.tone === "off"
                                  ? "var(--od-faint-5)"
                                  : "var(--od-text-3)",
                          }}
                        >
                          {item.label}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>

        <div className="text-od-faint mt-[14px] max-w-[74ch] text-[12.5px] text-pretty">
          {t.u_workspace_note}
        </div>
      </div>

      <div className="border-od-line bg-od-panel-deep-3 overflow-hidden rounded-[10px] border">
        <div className="p-[16px_18px_12px]">
          <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.u_roles_title}
          </h3>
          <div className="text-od-faint mt-[5px] text-[12.5px] text-pretty">{t.u_roles_note}</div>
        </div>

        <div className="overflow-x-auto">
          <div className="min-w-[640px]">
            <div
              className="border-od-line bg-od-canvas-2 text-od-faint grid gap-[14px] border-t border-b p-[10px_18px] text-[11px] tracking-[.06em] uppercase"
              style={{ gridTemplateColumns: ROLE_GRID }}
            >
              <span>{t.u_role_column}</span>
              {ROLE_COLUMNS.map((label) => (
                <span key={label} className="text-center text-pretty">
                  {t[label]}
                </span>
              ))}
            </div>

            {ROLE_MATRIX.map((row) => (
              <div
                key={row.role}
                className="grid items-center gap-[14px] border-b border-[color:var(--od-raise-6)] p-[13px_18px]"
                style={{ gridTemplateColumns: ROLE_GRID }}
              >
                <div className="min-w-0">
                  <div className="text-od-text font-medium">{t[ROLE_LABEL[row.role]]}</div>
                  <div className="text-od-faint mt-[2px] text-[12px] text-pretty">{t[row.note]}</div>
                </div>
                {row.cells.map((yes, index) => (
                  <span
                    key={index}
                    className="text-center text-[13px]"
                    style={{ color: yes ? "var(--od-green-text)" : "var(--od-faint-5)" }}
                  >
                    {yes ? "✓" : "—"}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

function ApiPanels({ t }: { t: SettingsDictionary }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
          <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.a_title}
          </h3>
          <span className="text-od-faint text-[12.5px] text-pretty">{t.a_note}</span>
        </div>
        <div
          dir="ltr"
          className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-3 rounded-[7px] border p-[11px_13px] text-[13px] [overflow-wrap:anywhere]"
        >
          https://telagent.wagner-partner.local/api/v1
        </div>

        <div className="mt-2 flex flex-col">
          {API_KEYS.map((key) => (
            <div
              key={key.id}
              className="border-od-border flex flex-wrap items-center gap-x-4 gap-y-[10px] border-b py-3"
            >
              <div className="min-w-[200px] flex-[1_1_240px]">
                <div className="text-od-text-3 font-medium text-pretty">{t[key.name]}</div>
                {/* Only the last characters are ever shown — the key itself is write-once. */}
                <div
                  dir="ltr"
                  className="mono ltr-data text-od-muted-5 mt-[3px] text-[12.5px] [overflow-wrap:anywhere]"
                >
                  {key.key}
                </div>
              </div>
              <span className="text-od-muted-5 text-[12.5px]">{t[key.used]}</span>
              <button
                type="button"
                className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px] hover:bg-[var(--od-raise-4)]"
              >
                {t.a_revoke}
              </button>
            </div>
          ))}
        </div>

        <button
          type="button"
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium"
        >
          {t.a_create}
        </button>
      </div>

      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
        <SectionHead title={t.w_title} note={t.w_note} />
        {WEBHOOKS.map((hook) => (
          <div
            key={hook.id}
            className="flex flex-wrap items-center gap-x-4 gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[14px_18px]"
          >
            <div className="min-w-[220px] flex-[1_1_260px]">
              <div
                dir="ltr"
                className="mono ltr-data text-od-text-3 text-[13px] [overflow-wrap:anywhere]"
              >
                {hook.url}
              </div>
              <div
                dir="ltr"
                className="mono ltr-data text-od-muted-5 mt-1 text-start text-[12.5px] [overflow-wrap:anywhere]"
              >
                {hook.events}
              </div>
            </div>
            <span
              className="rounded-[5px] border p-[2px_9px] text-[11.5px] font-semibold whitespace-nowrap"
              style={{
                borderColor: hook.ok ? "var(--od-green-border)" : "var(--od-red-border-3)",
                background: hook.ok ? "rgba(63,185,132,.10)" : "rgba(240,96,94,.10)",
                color: hook.ok ? "var(--od-green-text)" : "var(--od-red-text-4)",
              }}
            >
              {hook.ok ? t.w_healthy : t.w_failing}
            </span>
            <span className="text-od-faint text-[12.5px]">{t[hook.last]}</span>
          </div>
        ))}
        <div className="flex flex-wrap items-center justify-between gap-x-[18px] gap-y-3 border-t border-[color:var(--od-raise-6)] p-[14px_18px]">
          <span className="text-od-faint max-w-[60ch] text-[12.5px] text-pretty">
            {t.w_replay_note}
          </span>
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium"
          >
            {t.w_add}
          </button>
        </div>
      </div>
    </div>
  );
}

function McpPanels({ locale, t }: { locale: Locale; t: SettingsDictionary }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-[10px] border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.06)]">
        <div className="flex flex-wrap items-start justify-between gap-x-[18px] gap-y-3 p-[18px_18px_12px]">
          <div className="min-w-0 flex-[1_1_300px]">
            <h3 className="m-0 text-[13px] font-semibold tracking-[.07em] uppercase text-[color:var(--od-violet-3)]">
              {t.m_inward_title}
            </h3>
            <div className="text-od-muted-2 mt-[5px] max-w-[70ch] text-[12.5px] text-pretty">
              {t.m_inward_note}
            </div>
          </div>
          <Switch on />
        </div>

        <div className="p-[0_18px_14px]">
          <div
            dir="ltr"
            className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[11px_13px] text-[12.5px] [overflow-wrap:anywhere]"
          >
            https://telagent.wagner-partner.local/mcp
          </div>
        </div>

        {OUR_TOOLS.map((tool) => (
          <div
            key={tool.name}
            className="flex flex-wrap items-start gap-x-4 gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[12px_18px]"
          >
            <div className="min-w-[200px] flex-[1_1_240px]">
              <div dir="ltr" className="mono ltr-data text-od-text-3 text-[12.5px]">
                {tool.name}
              </div>
              <div className="text-od-muted-5 mt-[3px] max-w-[58ch] text-[12.5px] text-pretty">
                {t[tool.desc]}
              </div>
            </div>
            <span
              className="mt-[2px] flex-none rounded-[5px] border p-[2px_9px] text-[10.5px] font-bold tracking-[.05em] uppercase whitespace-nowrap"
              style={{
                borderColor:
                  tool.scope === "read"
                    ? "var(--od-border-7)"
                    : tool.scope === "act"
                      ? "var(--od-violet-border)"
                      : "var(--od-amber-border)",
                background:
                  tool.scope === "read"
                    ? "var(--od-raise-5)"
                    : tool.scope === "act"
                      ? "rgba(139,124,255,.12)"
                      : "var(--od-amber-bg)",
                color:
                  tool.scope === "read"
                    ? "var(--od-muted-5)"
                    : tool.scope === "act"
                      ? "var(--od-violet-3)"
                      : "var(--od-amber-text)",
              }}
            >
              {t[`scope_${tool.scope}`]}
            </span>
            <Switch on={tool.on} />
          </div>
        ))}

        {/* Why write is off by default, said plainly. */}
        <div className="border-t border-[color:var(--od-raise-6)] p-[14px_18px] text-[12.5px] text-pretty text-[color:var(--od-amber-text)]">
          {t.m_warning}
        </div>
      </div>

      <Link
        href={`/${locale}/connectors`}
        className="border-od-line bg-od-panel-deep-3 text-od-text-2 flex flex-wrap items-center justify-between gap-x-[18px] gap-y-3 rounded-[10px] border p-[16px_18px] hover:bg-[var(--od-raise-4)] hover:no-underline"
      >
        <span className="min-w-0 flex-[1_1_300px]">
          <span className="text-od-text block text-[15px] font-semibold">
            {t.m_outward_title}
          </span>
          <span className="text-od-muted-2 mt-[5px] block max-w-[66ch] text-[12.5px] text-pretty">
            {t.m_outward_note}
          </span>
        </span>
        <span className="flex-none text-[13px] whitespace-nowrap text-[color:var(--od-violet-3)]">
          {t.m_outward_link}
        </span>
      </Link>
    </div>
  );
}

function InviteDialog({ t, onClose }: { t: SettingsDictionary; onClose: () => void }) {
  const [role, setRole] = useState("reception");
  const [copied, setCopied] = useState(false);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-auto p-[60px_24px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div className="border-od-border-9 bg-od-panel w-full max-w-[520px] rounded-xl border">
        <div className="border-od-line flex flex-wrap items-start justify-between gap-x-5 gap-y-3 border-b p-[20px_22px]">
          <div className="min-w-0">
            <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.inv_title}</h2>
            <p className="text-od-muted-4 mt-[6px] max-w-[50ch] text-pretty">{t.inv_note}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_10px] hover:bg-[var(--od-raise-6)]"
          >
            {t.close}
          </button>
        </div>

        <div className="flex flex-col gap-[18px] p-[20px_22px]">
          <div>
            <div className="text-od-muted-5 text-[12.5px]">{t.inv_email_label}</div>
            <div
              dir="ltr"
              className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-faint-2 mt-[6px] rounded-[7px] border p-[10px_12px] text-[13.5px]"
            >
              name@wagner-partner.at
            </div>
          </div>

          <div>
            <div className="text-od-muted-5 text-[12.5px]">{t.inv_role_label}</div>
            <div className="mt-2 flex flex-col gap-2">
              {INVITE_ROLES.map((entry) => {
                const on = role === entry.id;
                return (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => setRole(entry.id)}
                    className="flex cursor-pointer items-start gap-[11px] rounded-[9px] border p-[12px_14px] text-start"
                    style={{
                      borderColor: on ? "var(--od-violet)" : "var(--od-border-7)",
                      background: on ? "var(--od-raise-10)" : "var(--od-canvas-2)",
                    }}
                  >
                    <span
                      className="mt-[3px] size-[15px] flex-none rounded-full border"
                      style={{
                        borderColor: on ? "var(--od-violet)" : "var(--od-stroke-5)",
                        background: on ? "var(--od-violet)" : "transparent",
                        boxShadow: on ? "inset 0 0 0 3px var(--od-panel)" : "none",
                      }}
                    />
                    <span className="min-w-0">
                      <span className="text-od-text-2 block font-semibold">{t[entry.label]}</span>
                      <span className="text-od-muted-5 mt-[3px] block text-[12.5px] text-pretty">
                        {t[entry.note]}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="border-od-line bg-od-panel-deep-2 rounded-[9px] border p-[13px_15px]">
            <div className="flex flex-wrap items-center justify-between gap-x-[14px] gap-y-[10px]">
              <div className="min-w-0">
                <div className="text-od-text-5 text-[13px] font-medium">
                  {t.inv_link_title}
                </div>
                <div
                  dir="ltr"
                  className="mono ltr-data text-od-muted-5 mt-[5px] text-[12px] [overflow-wrap:anywhere]"
                >
                  https://telagent.wagner-partner.local/invite/4F2K9QD1
                </div>
              </div>
              <button
                type="button"
                onClick={() => setCopied(true)}
                className="cursor-pointer rounded-[7px] border p-[8px_13px] text-[13px] font-medium whitespace-nowrap"
                style={{
                  borderColor: copied ? "var(--od-green-border)" : "var(--od-border-7)",
                  background: copied ? "rgba(63,185,132,.10)" : "transparent",
                  color: copied ? "var(--od-green-text)" : "var(--od-text-3)",
                }}
              >
                {copied ? t.inv_copied : t.inv_copy}
              </button>
            </div>
          </div>
        </div>

        <div className="border-od-line flex flex-wrap items-center justify-end gap-[10px] border-t p-[16px_22px]">
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px] whitespace-nowrap"
          >
            {t.cancel}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 inline-flex cursor-pointer items-center gap-[9px] rounded-[7px] border p-[9px_16px] font-semibold whitespace-nowrap"
          >
            {t.inv_send} <span className="mono ltr-data text-od-faint text-[11.5px]">⌘↵</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function ReadOnlyConfig({ t }: { t: SettingsDictionary }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.err_label}
        </div>
        <h2 className="mt-[18px] mb-0 text-[21px] font-semibold">{t.err_title}</h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">
          {t.err_body_before}
          <span className="mono">/config/telagent.yaml</span>
          {t.err_body_after}
        </p>
        <div className="mt-5 flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            {t.err_retry}
          </button>
          <button
            type="button"
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_16px]"
          >
            {t.err_how}
          </button>
        </div>
      </div>
    </div>
  );
}
