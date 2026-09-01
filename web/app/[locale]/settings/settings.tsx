"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { LiveSettings, type FieldCopy } from "@/components/settings/live-settings";
import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import {
  API_URL,
  ASSIGNABLE_ROLES,
  ApiError,
  accountEvents,
  addWebhook,
  changeMemberRole,
  changePassword,
  createInvite,
  currentUser,
  discordChannel,
  emailChannel,
  changeWebhook,
  membersList,
  metaChatChannel,
  mintToken,
  regenerateInvite,
  removeMember,
  removeToken,
  removeWebhook,
  rotateToken,
  rotateWebhookSecret,
  saveDiscordChannel,
  saveEmailChannel,
  saveMetaChatChannel,
  saveSlackChannel,
  saveTelegramChannel,
  saveWebChannel,
  saveWhatsAppChannel,
  sendTestMail,
  slackChannel,
  telegramChannel,
  testDiscordChannel,
  testEmailChannel,
  testMetaChatChannel,
  testSlackChannel,
  testTelegramChannel,
  testWhatsAppChannel,
  whatsappChannel,
  testModel,
  signOutEverywhereElse,
  tokenList,
  updateMyLocale,
  webChannel,
  webhookEvents,
  webhooksList,
  type AccountEvent,
  type InviteLink,
  type MachineScope,
  type Member,
  type MintedToken,
  type DiscordChannel,
  type EmailChannel,
  type MetaChatChannel,
  type MetaChatKind,
  type SlackChannel,
  type TelegramChannel,
  type WebChannel,
  type WhatsAppChannel,
  type WebhookWithSecret,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import {
  EVENT_LABEL,
  HOST_FIELDS,
  OUR_TOOLS,
  PAGE_LINKS,
  ROLE_COLUMNS,
  ROLE_LABEL,
  ROLE_MATRIX,
  SECTIONS,
  TABS,
  WEBHOOK_EVENT_LABEL,
  type Control,
  type Field,
  type Key,
  type Role,
} from "@/lib/settings/data";
import { useResource, type Resource } from "@/lib/use-resource";

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

/**
 * The model that answers — §B9.2, which puts a provider key in the encrypted column
 * rather than in `.env`. This is the field the whole product rests on: with nothing
 * here the agent takes messages and says so, which is honest but is not the product.
 *
 * The endpoint row replaced a drawn one on this same tab. It read
 * `http://localhost:8080/v1` and saved nowhere.
 */
function modelFields(t: SettingsDictionary): FieldCopy[] {
  return [
    { key: "llm.provider", label: t.f_llm_provider, help: t.f_llm_provider_help },
    { key: "llm.model", label: t.f_llm_model, help: t.f_llm_model_help },
    { key: "llm.api_key", label: t.f_llm_key, help: t.f_llm_key_help },
    { key: "llm.base_url", label: t.f_llm_base_url, help: t.f_llm_base_url_help },
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

/**
 * The one recording setting that exists (`recording.announce`, per workspace). The
 * retention rows below it are still drawings — nothing stores audio yet, so a
 * retention period would be a promise about data that cannot be kept or broken.
 */
function announceFields(t: SettingsDictionary): FieldCopy[] {
  return [{ key: "recording.announce", label: t.f_announce, help: t.f_announce_help }];
}

export function Settings({ locale, t }: { locale: Locale; t: SettingsDictionary }) {
  const router = useRouter();
  const [state, setState] = useState<ScreenState>("default");
  const [tab, setTab] = useState("general");

  const offline = state === "offline";
  const empty = state === "empty";
  // Optional now, and honestly so: a tab that links to its own screen has no section
  // here, and a tab that renders a wired panel may not need a heading either. The
  // previous type promised one for every string, which is how a tab was added without
  // one and crashed on `section.title` the first time it was clicked.
  const section = SECTIONS[tab as keyof typeof SECTIONS];

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[var(--od-shell-w)]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[var(--od-shell-w)]">
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
                    {section ? (
                      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
                        <h2 className="text-od-text m-0 text-[16px] font-semibold">
                          {t[section.title]}
                        </h2>
                        <p className="text-od-muted-4 mt-[6px] max-w-[64ch] text-pretty">
                          {t[section.body]}
                        </p>
                      </div>
                    ) : null}

                    {tab === "profile" ? <ProfilePanels t={t} /> : null}
                    {tab === "users" ? <UsersPanels t={t} /> : null}
                    {tab === "channels" ? <ChannelsPanels t={t} /> : null}
                    {tab === "api" ? <ApiPanels locale={locale} t={t} /> : null}
                    {tab === "mcp" ? (
                      <McpPanels locale={locale} t={t} onOpenApiTab={() => setTab("api")} />
                    ) : null}
                    {tab === "advanced" ? (
                      <>
                        <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
                          <SectionHead title={t.model_title} note={t.live_note} />
                          <LiveSettings fields={modelFields(t)} labels={liveLabels(t)} />
                          <ModelTestRow t={t} />
                        </div>
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
                        <MailTestRow t={t} />
                      </div>
                    ) : null}
                    {tab === "recording" ? (
                      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
                        <SectionHead title={t.announce_title} note={t.live_note} />
                        <LiveSettings fields={announceFields(t)} labels={liveLabels(t)} />
                      </div>
                    ) : null}

                    {section && section.fields.length > 0 ? (
                      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
                        {section.fields.map((field) => (
                          <div key={field.id} className="border-b border-[color:var(--od-raise-6)]">
                            <FieldRow t={t} field={field} first />
                          </div>
                        ))}
                      </div>
                    ) : null}

                    {/* The drawn Save/Discard pair belongs to the tabs whose forms
                        are still drawings. A wired tab saves through its own
                        controls, and a second pair that saves nothing would undo
                        the honesty the wiring bought. */}
                    {["profile", "users", "notifications", "api"].includes(tab) ? null : (
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
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}

/** A language is named in itself, never translated. */
const LANGUAGES: { id: "en" | "de" | "ar"; endonym: string }[] = [
  { id: "en", endonym: "English" },
  { id: "de", endonym: "Deutsch" },
  { id: "ar", endonym: "العربية" },
];

/**
 * The model panel's proof: ask for one token and close the stream.
 *
 * Two of the five outcomes carry something only the server knows — which field is
 * empty, what status the endpoint returned — so those show the translated sentence
 * *and* the machine's own words beside it, kept apart the way #76 settled.
 */
function ModelTestRow({ t }: { t: SettingsDictionary }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ text: string; machine?: string; bad?: boolean } | null>(
    null,
  );

  async function run() {
    setBusy(true);
    setResult(null);
    try {
      const outcome = await testModel();
      setResult({ text: t.model_test_reached, machine: outcome.model });
    } catch (thrown) {
      if (thrown instanceof ApiError) {
        if (thrown.code === "llm_not_configured") {
          setResult({ text: t.model_test_not_configured, bad: true });
        } else if (thrown.code === "llm_incomplete") {
          setResult({ text: t.model_test_incomplete, machine: thrown.message, bad: true });
        } else if (thrown.code === "llm_refused") {
          setResult({ text: t.model_test_refused, machine: thrown.message, bad: true });
        } else if (thrown.code === "llm_unreachable") {
          setResult({ text: t.model_test_unreachable, bad: true });
        } else {
          setResult({ text: thrown.message, bad: true });
        }
      } else {
        setResult({ text: thrown instanceof Error ? thrown.message : String(thrown), bad: true });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-[18px] gap-y-3 border-t border-[color:var(--od-raise-6)] p-[14px_18px]">
      <div
        className="max-w-[60ch] text-[13px] text-pretty"
        style={{ color: result?.bad ? "var(--od-red-text-6)" : "var(--od-muted-5)" }}
      >
        {result ? (
          <>
            {result.text}
            {result.machine ? (
              <>
                {" "}
                <span dir="ltr" className="mono ltr-data">
                  {result.machine}
                </span>
              </>
            ) : null}
          </>
        ) : null}
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={() => void run()}
        className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? t.model_testing : t.model_test}
      </button>
    </div>
  );
}

/**
 * The mail panel's proof: send a test to the signed-in admin's own address.
 * Never a typed one — a form that mails an arbitrary address is a spam relay.
 */
function MailTestRow({ t }: { t: SettingsDictionary }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ text: string; address?: string; bad?: boolean } | null>(
    null,
  );

  async function run() {
    setBusy(true);
    setResult(null);
    try {
      const outcome = await sendTestMail();
      setResult({ text: t.mail_test_sent, address: outcome.to });
    } catch (thrown) {
      if (thrown instanceof ApiError) {
        if (thrown.code === "mail_not_configured") {
          setResult({ text: t.mail_test_not_configured, bad: true });
        } else if (thrown.code === "no_email_on_account") {
          setResult({ text: t.mail_test_no_email, bad: true });
        } else if (thrown.code === "mail_failed") {
          setResult({ text: t.mail_test_failed, bad: true });
        } else {
          setResult({ text: thrown.message, bad: true });
        }
      } else {
        setResult({ text: thrown instanceof Error ? thrown.message : String(thrown), bad: true });
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-x-[18px] gap-y-3 border-t border-[color:var(--od-raise-6)] p-[14px_18px]">
      <div
        className="max-w-[60ch] text-[13px] text-pretty"
        style={{ color: result?.bad ? "var(--od-red-text-6)" : "var(--od-muted-5)" }}
      >
        {result ? (
          <>
            {result.text}
            {result.address ? (
              <>
                {" "}
                <span dir="ltr" className="mono ltr-data">
                  {result.address}
                </span>
              </>
            ) : null}
          </>
        ) : null}
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={() => void run()}
        className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? t.mail_test_sending : t.smtp_send_test}
      </button>
    </div>
  );
}

/**
 * The profile tab's identity and security panels, wired to `api/routes/auth.py`.
 *
 * What went, and why: the avatar upload and the two-factor row were fixture controls
 * with no endpoint behind them, and the 2FA note claimed an enforcement ("required to
 * change routing rules") that exists nowhere. A control with no endpoint is removed,
 * not drawn. The name/email/language form below stays a drawing — there is no
 * profile-update endpoint yet, and this component does not touch it.
 */
function ProfilePanels({ t }: { t: SettingsDictionary }) {
  const router = useRouter();
  const me = useResource(() => currentUser());
  const events = useResource(() => accountEvents());
  const [changeOpen, setChangeOpen] = useState(false);
  const [ending, setEnding] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  // The one line of feedback under the security panel: what the last action did.
  const [outcome, setOutcome] = useState<{ text: string; bad?: boolean } | null>(null);

  if (me.data === null && me.loading) {
    return <p className="text-od-muted-5 p-[14px_18px] text-[13px]">{t.live_loading}</p>;
  }
  if (me.data === null) {
    return (
      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[14px_18px]">
        <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
          {me.error?.message ?? t.live_failed}
        </p>
        <button
          type="button"
          onClick={me.reload}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
        >
          {t.live_retry}
        </button>
      </div>
    );
  }

  const account = me.data;
  const roleKey = account.workspaces[0]
    ? ROLE_LABEL[account.workspaces[0].role as Role]
    : undefined;
  // The server returns most recent first, so `find` is "the last time it happened".
  // A reset by code and a signed-in change are the same fact to this line.
  const lastChange = events.data?.find(
    (entry) => entry.event === "password_changed" || entry.event === "password_reset",
  );

  async function switchLanguage(next: "en" | "de" | "ar") {
    if (switching !== null || next === account.locale) return;
    setSwitching(next);
    setOutcome(null);
    try {
      await updateMyLocale(next);
      // The dictionary is rendered per locale on the server, so the change is a
      // navigation into the same screen under the new prefix.
      router.push(`/${next}/settings`);
    } catch (thrown) {
      setOutcome({ text: thrown instanceof Error ? thrown.message : String(thrown), bad: true });
      setSwitching(null);
    }
  }

  async function endOtherSessions() {
    setEnding(true);
    setOutcome(null);
    try {
      await signOutEverywhereElse();
      setOutcome({ text: t.p_signout_done });
      events.reload();
    } catch (thrown) {
      setOutcome({ text: thrown instanceof Error ? thrown.message : String(thrown), bad: true });
    } finally {
      setEnding(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="border-od-line bg-od-panel-deep-3 flex flex-wrap items-center gap-[18px] rounded-[10px] border p-[18px]">
        <span className="border-od-border-9 text-od-text-3 inline-flex size-16 flex-none items-center justify-center rounded-full border bg-[var(--od-raise-5)] text-[22px] font-semibold">
          {account.username.charAt(0).toUpperCase()}
        </span>
        <div className="min-w-[200px] flex-[1_1_240px]">
          <div className="text-od-text text-[16px] font-semibold">{account.username}</div>
          {roleKey ? (
            <div className="text-od-muted-5 mt-[3px] text-[13px]">
              {interpolate(t.p_signed_in, { role: t[roleKey] })}
            </div>
          ) : null}
          {account.email ? (
            <div dir="ltr" className="mono ltr-data text-od-muted-5 mt-[3px] text-start text-[12.5px] [overflow-wrap:anywhere]">
              {account.email}
            </div>
          ) : null}
        </div>
      </div>

      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <div className="flex flex-wrap items-center gap-x-[18px] gap-y-3">
          <div className="min-w-[220px] flex-[1_1_260px]">
            <div className="text-od-text-3 font-medium">{t.f_lang}</div>
            <div className="text-od-muted-5 mt-[3px] text-[13px] text-pretty">
              {t.f_lang_help_profile}
            </div>
          </div>
          <div className="ms-auto flex flex-wrap gap-2">
            {LANGUAGES.map((language) => {
              const on = account.locale === language.id;
              const pending = switching === language.id;
              return (
                <button
                  key={language.id}
                  type="button"
                  disabled={switching !== null}
                  onClick={() => void switchLanguage(language.id)}
                  className="cursor-pointer rounded-full border p-[7px_13px] text-[13px] whitespace-nowrap disabled:cursor-not-allowed"
                  style={{
                    borderColor:
                      on || pending ? "var(--od-violet-border)" : "var(--od-border-7)",
                    background: on || pending ? "rgba(139,124,255,.14)" : "transparent",
                    color: on || pending ? "var(--od-violet-3)" : "var(--od-muted-4)",
                    fontWeight: on ? 500 : 400,
                    opacity: switching !== null && !pending ? 0.5 : 1,
                  }}
                >
                  {language.endonym}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <h3 className="text-od-muted-4 mt-0 mb-3 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.p_security}
        </h3>

        <div className="border-od-border flex flex-wrap items-center gap-x-[18px] gap-y-3 border-b py-[13px]">
          <div className="min-w-[220px] flex-[1_1_260px]">
            <div className="text-od-text-3 font-medium">{t.p_password}</div>
            {/* Read from the account's own trail. When no change is in the last 50
                events the line is absent — "4 months ago" with nothing behind it is
                exactly the kind of drawing this panel stopped being. */}
            {lastChange ? (
              <div className="text-od-muted-5 mt-[3px] text-[13px]">
                {interpolate(t.p_password_last_on, {
                  date: lastChange.created_at.slice(0, 10),
                })}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => setChangeOpen(true)}
            className="border-od-border-7 text-od-text-3 ms-auto cursor-pointer rounded-md border bg-transparent p-[8px_14px] text-[13px] whitespace-nowrap hover:bg-[var(--od-raise-4)]"
          >
            {t.p_change_password}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-x-[18px] gap-y-3 py-[13px]">
          <div className="min-w-[220px] flex-[1_1_260px]">
            <div className="text-od-text-3 font-medium">{t.p_devices}</div>
            <div className="text-od-muted-5 mt-[3px] text-[13px] text-pretty">
              {t.p_devices_note}
            </div>
          </div>
          <button
            type="button"
            disabled={ending}
            onClick={endOtherSessions}
            className="ms-auto cursor-pointer border-none bg-transparent p-0 text-[13px] text-[color:var(--od-red-text-4)] hover:underline disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t.p_signout_all}
          </button>
        </div>

        {outcome ? (
          <div
            className="mt-[6px] text-[13px] text-pretty"
            style={{ color: outcome.bad ? "var(--od-red-text-6)" : "var(--od-muted-5)" }}
          >
            {outcome.text}
          </div>
        ) : null}
      </div>

      <ActivityPanel t={t} events={events} />

      {changeOpen ? (
        <ChangePasswordDialog
          t={t}
          onClose={() => setChangeOpen(false)}
          onChanged={() => {
            setChangeOpen(false);
            setOutcome({ text: t.p_changed_done });
            events.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function ActivityPanel({
  t,
  events,
}: {
  t: SettingsDictionary;
  events: Resource<AccountEvent[]>;
}) {
  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <h3 className="text-od-muted-4 mt-0 mb-1 text-[13px] font-semibold tracking-[.07em] uppercase">
        {t.act_title}
      </h3>
      <div className="text-od-faint mb-2 text-[12.5px] text-pretty">{t.act_note}</div>

      {events.data === null && events.loading ? (
        <p className="text-od-muted-5 m-0 py-[10px] text-[13px]">{t.live_loading}</p>
      ) : events.data === null ? (
        <div className="py-[10px]">
          <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
            {events.error?.message ?? t.live_failed}
          </p>
          <button
            type="button"
            onClick={events.reload}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
          >
            {t.live_retry}
          </button>
        </div>
      ) : events.data.length === 0 ? (
        <p className="text-od-muted-5 m-0 py-[10px] text-[13px]">{t.act_empty}</p>
      ) : (
        <div className="flex flex-col">
          {events.data.slice(0, 10).map((entry, index) => {
            const label = EVENT_LABEL[entry.event];
            return (
              <div
                key={`${entry.created_at}-${index}`}
                className={`flex flex-wrap items-baseline gap-x-4 gap-y-1 py-[9px] ${
                  index === 0 ? "" : "border-t border-[color:var(--od-raise-6)]"
                }`}
              >
                <div className="min-w-[180px] flex-[1_1_220px]">
                  {label ? (
                    <span className="text-od-text-3 text-[13.5px]">{t[label]}</span>
                  ) : (
                    // Outside the known vocabulary: machine output, shown as such.
                    <span dir="ltr" className="mono ltr-data text-od-text-3 text-[13px]">
                      {entry.event}
                    </span>
                  )}
                  {entry.ip ? (
                    <span dir="ltr" className="mono ltr-data text-od-faint ms-[10px] text-[12px]">
                      {entry.ip}
                    </span>
                  ) : null}
                </div>
                <span dir="ltr" className="mono ltr-data text-od-faint-2 flex-none text-[12px] whitespace-nowrap">
                  {entry.created_at.slice(0, 16).replace("T", " ")}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ChangePasswordDialog({
  t,
  onClose,
  onChanged,
}: {
  t: SettingsDictionary;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [fresh, setFresh] = useState("");
  const [repeat, setRepeat] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    // The one check the browser can make. Everything else — the current password,
    // the policy, the history — is the server's answer, mapped by `code` below.
    if (fresh !== repeat) {
      setError(t.pw_mismatch);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await changePassword(current, fresh);
      onChanged();
    } catch (thrown) {
      if (thrown instanceof ApiError) {
        if (thrown.code === "unauthenticated") setError(t.pw_wrong_current);
        else if (thrown.code === "password_reused") setError(t.pw_reused);
        else if (thrown.code === "password_too_short") setError(t.pw_too_short);
        else if (thrown.code === "rate_limited") setError(t.pw_locked);
        else setError(thrown.message);
      } else {
        setError(thrown instanceof Error ? thrown.message : String(thrown));
      }
    } finally {
      setPending(false);
    }
  }

  const field =
    "border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[6px] w-full rounded-[7px] border p-[10px_12px] text-[13.5px]";

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-auto p-[60px_24px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div className="border-od-border-9 bg-od-panel w-full max-w-[460px] rounded-xl border">
        <div className="border-od-line flex flex-wrap items-start justify-between gap-x-5 gap-y-3 border-b p-[20px_22px]">
          <div className="min-w-0">
            <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.p_change_password}</h2>
            <p className="text-od-muted-4 mt-[6px] max-w-[50ch] text-pretty">{t.pw_hint}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_10px] hover:bg-[var(--od-raise-6)]"
          >
            {t.close}
          </button>
        </div>

        <form
          className="flex flex-col gap-[16px] p-[20px_22px]"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="block">
            <span className="text-od-muted-5 text-[12.5px]">{t.pw_current}</span>
            <input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              className={field}
            />
          </label>
          <label className="block">
            <span className="text-od-muted-5 text-[12.5px]">{t.pw_new}</span>
            <input
              type="password"
              autoComplete="new-password"
              value={fresh}
              onChange={(event) => setFresh(event.target.value)}
              className={field}
            />
          </label>
          <label className="block">
            <span className="text-od-muted-5 text-[12.5px]">{t.pw_repeat}</span>
            <input
              type="password"
              autoComplete="new-password"
              value={repeat}
              onChange={(event) => setRepeat(event.target.value)}
              className={field}
            />
          </label>

          {error ? (
            <p className="m-0 text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
              {error}
            </p>
          ) : null}

          <div className="flex items-center justify-end gap-[10px]">
            <button
              type="submit"
              disabled={pending || !current || !fresh || !repeat}
              className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
            >
              {pending ? t.live_saving : t.p_change_password}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * The team list, wired to `/api/members`.
 *
 * What went, and why: the "Invite member" button and its dialog were a drawing of a
 * flow whose decisions are not taken (who picks the username, what the link token
 * is), and the phone chips, extension numbers and "also in Wolf Studio" lines
 * described phones and cross-workspace visibility that do not exist. A control with
 * no endpoint is removed, not drawn. The owner's row and the reader's own row carry
 * no menu — the server refuses both, and a menu of refusals is not a menu.
 */
/**
 * Inviting somebody in — D-034, the admin half.
 *
 * Two phases in one dialog: the form (an email address and a role, nothing else),
 * and the issued link. The link is shown once and shown always — when mail is
 * configured a copy also goes by email, and when it is not, "copy it and send it
 * yourself" is the designed answer rather than a failure.
 */
function InviteDialog({
  t,
  issued,
  onIssued,
  onClose,
}: {
  t: SettingsDictionary;
  issued: InviteLink | null;
  onIssued: (link: InviteLink) => void;
  onClose: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "reception" | "viewer">("reception");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const ROLE_NOTES: Record<"admin" | "reception" | "viewer", { label: string; note: string }> = {
    admin: { label: t[ROLE_LABEL.admin], note: t.inv_admin_note },
    reception: { label: t[ROLE_LABEL.reception], note: t.inv_reception_note },
    viewer: { label: t[ROLE_LABEL.viewer], note: t.inv_viewer_note },
  };

  const link =
    issued === null || typeof window === "undefined"
      ? null
      : `${window.location.origin}${issued.invite_path}`;

  async function submit() {
    setPending(true);
    setError(null);
    try {
      const created = await createInvite(email.trim(), role);
      onIssued(created);
      setCopied(false);
    } catch (thrown) {
      if (thrown instanceof ApiError && thrown.code === "invalid_email") {
        setError(t.inv_err_email);
      } else {
        setError(thrown instanceof Error ? thrown.message : String(thrown));
      }
    } finally {
      setPending(false);
    }
  }

  async function copy() {
    if (link === null) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      // Clipboard can be unavailable; the link is on screen to select by hand.
    }
  }

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

        {issued === null ? (
          <form
            className="flex flex-col gap-[18px] p-[20px_22px]"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <label className="block">
              <span className="text-od-muted-5 text-[12.5px]">{t.inv_email_label}</span>
              <input
                dir="ltr"
                type="email"
                autoComplete="off"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[6px] w-full rounded-[7px] border p-[10px_12px] text-[13.5px]"
              />
            </label>

            <div>
              <div className="text-od-muted-5 text-[12.5px]">{t.inv_role_label}</div>
              <div className="mt-2 flex flex-col gap-2">
                {(["admin", "reception", "viewer"] as const).map((entry) => {
                  const on = role === entry;
                  return (
                    <button
                      key={entry}
                      type="button"
                      onClick={() => setRole(entry)}
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
                        <span className="text-od-text-2 block font-semibold">
                          {ROLE_NOTES[entry].label}
                        </span>
                        <span className="text-od-muted-5 mt-[3px] block text-[12.5px] text-pretty">
                          {ROLE_NOTES[entry].note}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            {error ? (
              <p className="m-0 text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
                {error}
              </p>
            ) : null}

            <div className="flex items-center justify-end gap-[10px]">
              <button
                type="submit"
                disabled={pending || !email.trim()}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
              >
                {pending ? t.inv_creating : t.inv_create}
              </button>
            </div>
          </form>
        ) : (
          <div className="flex flex-col gap-[16px] p-[20px_22px]">
            <div className="border-od-line bg-od-panel-deep-2 rounded-[9px] border p-[13px_15px]">
              <div className="flex flex-wrap items-center justify-between gap-x-[14px] gap-y-[10px]">
                <div className="min-w-0">
                  <div className="text-od-text-5 text-[13px] font-medium">{t.inv_link_title}</div>
                  <div
                    dir="ltr"
                    className="mono ltr-data text-od-muted-5 mt-[5px] text-start text-[12px] [overflow-wrap:anywhere]"
                  >
                    {link}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => void copy()}
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

            <p className="text-od-muted-5 m-0 text-[13px] text-pretty">
              {issued.mailed ? t.inv_mailed : t.inv_not_mailed}
            </p>
            <p className="text-od-faint m-0 text-[12.5px] text-pretty">{t.inv_expires}</p>

            <div className="flex items-center justify-end">
              <button
                type="button"
                onClick={onClose}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
              >
                {t.inv_done}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function UsersPanels({ t }: { t: SettingsDictionary }) {
  const members = useResource<Member[]>(() => membersList());
  const me = useResource(() => currentUser());
  const [menuFor, setMenuFor] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // The invite dialog: open for a new invitation, or showing a freshly issued link.
  const [inviteOpen, setInviteOpen] = useState(false);
  const [issued, setIssued] = useState<InviteLink | null>(null);

  function openMenu(userId: number | null) {
    setMenuFor(userId);
    setConfirming(null);
  }

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      openMenu(null);
      members.reload();
    } catch (thrown) {
      setNotice(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-[10px]">
          <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.u_team}
          </h3>
          <button
            type="button"
            onClick={() => {
              setIssued(null);
              setInviteOpen(true);
            }}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium whitespace-nowrap"
          >
            {t.u_invite}
          </button>
        </div>

        {members.data === null && members.loading ? (
          <p className="text-od-muted-5 m-0 py-[14px] text-[13px]">{t.live_loading}</p>
        ) : members.data === null ? (
          <div className="py-[14px]">
            <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
              {members.error?.message ?? t.live_failed}
            </p>
            <button
              type="button"
              onClick={members.reload}
              className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
            >
              {t.live_retry}
            </button>
          </div>
        ) : (
          <div className="mt-[10px] flex flex-col">
            {members.data.map((member) => {
              const invited = member.role === "invited";
              // The server refuses acting on the owner and on your own row; a menu
              // of refusals is not a menu, so those rows simply have none.
              const actionable =
                member.role !== "owner" && me.data !== null && member.user_id !== me.data.id;
              return (
                <div
                  key={member.user_id}
                  className="border-od-border flex flex-wrap items-center gap-x-[18px] gap-y-3 border-b py-[13px]"
                >
                  <span className="border-od-border-9 text-od-text-3 inline-flex size-8 flex-none items-center justify-center rounded-full border bg-[var(--od-raise-5)] text-[12.5px] font-semibold">
                    {member.username.charAt(0).toUpperCase()}
                  </span>
                  <div className="min-w-[180px] flex-[1_1_220px]">
                    <div className="flex flex-wrap items-center gap-[9px]">
                      <span className="text-od-text font-medium">{member.username}</span>
                      <span
                        className="rounded-md border p-[2px_9px] text-[12px] font-medium"
                        style={{
                          borderColor: invited ? "var(--od-amber-border)" : "var(--od-border-7)",
                          background: invited ? "var(--od-amber-bg)" : "var(--od-raise-5)",
                          color: invited ? "var(--od-amber-text)" : "var(--od-muted-4)",
                        }}
                      >
                        {t[ROLE_LABEL[member.role as Role]]}
                      </span>
                      {invited ? (
                        <span className="border-od-border-2 text-od-faint rounded-[5px] border p-[1px_8px] text-[11px] font-semibold whitespace-nowrap">
                          {t.u_not_active}
                        </span>
                      ) : null}
                    </div>
                    {member.email ? (
                      <div
                        dir="ltr"
                        className="text-od-muted-5 mt-[3px] text-start text-[12.5px] [overflow-wrap:anywhere]"
                      >
                        {member.email}
                      </div>
                    ) : null}
                  </div>

                  {actionable ? (
                    <div className="relative ms-auto">
                      <button
                        type="button"
                        onClick={() => openMenu(menuFor === member.user_id ? null : member.user_id)}
                        aria-label={t.u_more}
                        className="text-od-muted-4 hover:text-od-text-3 inline-flex size-[30px] cursor-pointer items-center justify-center rounded-[7px] border border-transparent bg-transparent text-[17px] leading-none hover:bg-[var(--od-raise-5)]"
                      >
                        ⋯
                      </button>
                      {menuFor === member.user_id ? (
                        <div
                          className="border-od-border-9 bg-od-panel absolute top-[34px] end-0 z-50 flex w-[208px] flex-col gap-px rounded-[9px] border p-[5px]"
                          style={{ boxShadow: "0 12px 28px var(--od-scrim-4)" }}
                        >
                          {invited ? (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                void act(async () => {
                                  const link = await regenerateInvite(member.user_id);
                                  setIssued(link);
                                  setInviteOpen(true);
                                })
                              }
                              className="text-od-text-3 cursor-pointer rounded-md border-none bg-transparent p-[8px_10px] text-start text-[13.5px] hover:bg-[var(--od-raise-5)] disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {t.u_resend}
                            </button>
                          ) : (
                            <>
                              <div className="p-[6px_10px_3px] text-[10.5px] tracking-[.08em] uppercase text-[color:var(--od-faint-5)]">
                                {t.u_change_perms}
                              </div>
                              {ASSIGNABLE_ROLES.filter((role) => role !== member.role).map(
                                (role) => (
                                  <button
                                    key={role}
                                    type="button"
                                    disabled={busy}
                                    onClick={() =>
                                      void act(() => changeMemberRole(member.user_id, role))
                                    }
                                    className="text-od-text-3 cursor-pointer rounded-md border-none bg-transparent p-[8px_10px] text-start text-[13.5px] hover:bg-[var(--od-raise-5)] disabled:cursor-not-allowed disabled:opacity-50"
                                  >
                                    {t[ROLE_LABEL[role]]}
                                  </button>
                                ),
                              )}
                              <div className="bg-od-border m-[4px_2px] h-px" />
                            </>
                          )}
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              confirming === member.user_id
                                ? void act(() => removeMember(member.user_id))
                                : setConfirming(member.user_id)
                            }
                            className="cursor-pointer rounded-md border-none bg-transparent p-[8px_10px] text-start text-[13.5px] hover:bg-[var(--od-raise-5)] disabled:cursor-not-allowed disabled:opacity-50"
                            style={{ color: "var(--od-red-text-4)" }}
                          >
                            {confirming === member.user_id
                              ? t.u_confirm_remove
                              : invited
                                ? t.u_cancel_invite
                                : t.u_remove}
                          </button>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}

        {notice ? (
          <div className="mt-3 text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
            {notice}
          </div>
        ) : null}

        <div className="text-od-faint mt-[14px] max-w-[74ch] text-[12.5px] text-pretty">
          {t.u_workspace_note}
        </div>
      </div>

      {inviteOpen ? (
        <InviteDialog
          t={t}
          issued={issued}
          onIssued={(link) => {
            setIssued(link);
            members.reload();
          }}
          onClose={() => setInviteOpen(false)}
        />
      ) : null}

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

/**
 * §A6.8's Channels tab. One card per channel; web chat is the only one built.
 *
 * The allowlist is the piece to get right in the interface, because it is the guard
 * (§B14) and it does not look like one. A person reads "sites allowed to show it" as a
 * convenience and leaves it empty; the help text under it says what an empty list
 * actually means, and the switch cannot be turned on until there is an entry - so the
 * screen refuses the same thing the server refuses, in the same words.
 */
function ChannelsPanels({ t }: { t: SettingsDictionary }) {
  const channel = useResource<WebChannel>(() => webChannel(), []);

  // A textarea, not a tag editor. Somebody with four domains pastes four lines; a chip
  // control would make them click four times to do it.
  const [origins, setOrigins] = useState<string | null>(null);
  const [siteKey, setSiteKey] = useState<string | null>(null);
  const [secret, setSecret] = useState("");
  const [threshold, setThreshold] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const row = channel.data;
  // The saved value until somebody types; then what they typed. `null` is "untouched",
  // which is why these are not initialised from `row` in an effect.
  const originsText = origins ?? (row ? row.allowed_origins.join("\n") : "");
  const siteKeyText = siteKey ?? row?.recaptcha_site_key ?? "";
  const thresholdText = threshold ?? String(row?.recaptcha_threshold ?? 0.5);

  const lines = originsText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "invalid_origin") return t.wc_error_origin;
      if (thrown.code === "no_allowed_origins") return t.wc_error_no_origins;
      if (thrown.code === "encryption_key_missing") return t.wc_error_no_key;
      return thrown.message;
    }
    return String(thrown);
  };

  const save = async (extra: Partial<Parameters<typeof saveWebChannel>[0]> = {}) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    try {
      await saveWebChannel({
        allowed_origins: lines,
        recaptcha_site_key: siteKeyText || null,
        recaptcha_threshold: Number(thresholdText) || 0.5,
        // Omitted when untouched: the server ignores an echoed mask, and not sending
        // one is the half that does not rely on it doing so.
        ...(secret === "" ? {} : { recaptcha_secret: secret }),
        ...extra,
      });
      setSecret("");
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  if (channel.error !== null && row === null) {
    return (
      <div className="border-od-red-border bg-od-red-bg rounded-[10px] border p-[18px]">
        <p className="m-0 text-pretty text-[color:var(--od-red-text-2)]">
          {channel.error.message}
        </p>
      </div>
    );
  }
  if (row === null) return null;

  return (
    <div className="flex flex-col gap-4">
      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
          <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.wc_title}
          </h3>
          <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
            {t.wc_note}
          </span>
        </div>

        {/* The snippet first: it is what the person came here for. */}
        <label className="mt-4 block">
          <span className="text-od-text-3 text-[13px] font-medium">{t.wc_snippet_label}</span>
          <div className="mt-2 flex flex-wrap items-start gap-2">
            <code
              dir="ltr"
              className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 min-w-[240px] flex-[1_1_320px] rounded-[7px] border p-[11px_13px] text-start text-[12.5px] [overflow-wrap:anywhere]"
            >
              {row.embed_snippet}
            </code>
            <button
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(row.embed_snippet);
                  setCopied(true);
                } catch {
                  // A denied clipboard is not an error worth a red box - the snippet
                  // is on screen and can be selected.
                  setCopied(false);
                }
              }}
              className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[10px_15px] text-[13px] font-semibold whitespace-nowrap"
            >
              {copied ? t.wc_copied : t.wc_copy}
            </button>
          </div>
          <span className="text-od-faint-2 mt-[6px] block text-[12.5px]">
            {t.wc_snippet_help}
          </span>
        </label>

        <label className="mt-5 block">
          <span className="text-od-text-3 text-[13px] font-medium">{t.wc_origins_label}</span>
          <textarea
            dir="ltr"
            value={originsText}
            onChange={(event) => setOrigins(event.target.value)}
            placeholder={t.wc_origins_placeholder}
            rows={4}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full resize-y rounded-[7px] border p-[11px_13px] text-start text-[13px]"
          />
          <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
            {t.wc_origins_help}
          </span>
        </label>

        <div className="border-od-border mt-4 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t pt-4">
          <div className="min-w-[220px] flex-[1_1_320px]">
            <div className="text-od-text-3 text-[13px] font-medium">{t.wc_enabled}</div>
            {lines.length === 0 ? (
              <div className="text-od-faint-2 mt-[3px] text-[12.5px]">
                {t.wc_enabled_off_help}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            disabled={busy || (lines.length === 0 && !row.enabled)}
            onClick={() => save({ enabled: !row.enabled })}
            className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
          >
            {row.enabled ? t.wc_enabled : t.wc_enabled}
            {row.enabled ? " ✓" : ""}
          </button>
        </div>
      </div>

      <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.wc_captcha_heading}
        </h3>
        <p className="text-od-faint mt-[8px] max-w-[64ch] text-pretty text-[12.5px]">
          {t.wc_captcha_help}
        </p>

        <div className="mt-4 flex flex-wrap gap-4">
          <label className="min-w-[200px] flex-[1_1_260px]">
            <span className="text-od-text-3 text-[13px] font-medium">{t.wc_site_key}</span>
            <input
              dir="ltr"
              value={siteKeyText}
              onChange={(event) => setSiteKey(event.target.value)}
              className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
            />
          </label>

          <label className="min-w-[200px] flex-[1_1_260px]">
            <span className="text-od-text-3 text-[13px] font-medium">{t.wc_secret}</span>
            <input
              dir="ltr"
              type="password"
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
              placeholder={row.recaptcha_secret_preview ?? ""}
              className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
            />
            <span className="text-od-faint-2 mt-[6px] block text-[12.5px]">
              {row.recaptcha_secret_preview ? t.wc_secret_keep : t.wc_secret_help}
            </span>
          </label>

          <label className="min-w-[120px] flex-[0_1_160px]">
            <span className="text-od-text-3 text-[13px] font-medium">{t.wc_threshold}</span>
            <input
              dir="ltr"
              type="number"
              min={0}
              max={1}
              step={0.1}
              value={thresholdText}
              onChange={(event) => setThreshold(event.target.value)}
              className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
            />
            <span className="text-od-faint-2 mt-[6px] block text-[12.5px]">
              {t.wc_threshold_help}
            </span>
          </label>
        </div>

        {row.recaptcha_secret_preview ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => save({ recaptcha_secret: "" })}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 mt-3 cursor-pointer rounded-md border bg-transparent p-[6px_11px] text-[12.5px]"
          >
            {t.wc_secret_clear}
          </button>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-3">
        {problem !== null ? (
          <span className="me-auto max-w-[52ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
            {problem}
          </span>
        ) : saved ? (
          <span className="text-od-muted-5 me-auto text-[13px]">{t.wc_saved}</span>
        ) : null}
        <button
          type="button"
          disabled={busy}
          onClick={() => save()}
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? t.wc_saving : t.wc_save}
        </button>
      </div>

      <TelegramCard t={t} />
      <EmailCard t={t} />
      <WhatsAppCard t={t} />
      <MetaChatCard
        t={t}
        kind="messenger"
        words={{
          title: t.ms_title,
          note: t.ms_note,
          accountId: t.ms_account_id,
          knownAs: t.ms_known_as,
        }}
      />
      <MetaChatCard
        t={t}
        kind="instagram"
        words={{
          title: t.ig_title,
          note: t.ig_note,
          accountId: t.ig_account_id,
          knownAs: t.ig_known_as,
        }}
      />
      <DiscordCard t={t} />
      <SlackCard t={t} />
    </div>
  );
}

/**
 * The Discord card — the Telegram card's contract: one bot token from the
 * customer's own developer portal. The one thing this card cannot do it says
 * plainly: the MESSAGE CONTENT intent lives in the portal, and a bot without it
 * hears every message as empty.
 */
function DiscordCard({ t }: { t: SettingsDictionary }) {
  const channel = useResource<DiscordChannel>(() => discordChannel(), []);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const row = channel.data;

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "no_bot_token") return t.dc_error_no_token;
      if (thrown.code === "encryption_key_missing") return t.wc_error_no_key;
      if (thrown.code === "discord_refused") return t.dc_error_refused;
      return thrown.message;
    }
    return String(thrown);
  };

  const act = async (run: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    setTested(null);
    try {
      await run();
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  if (row === null) return null;

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.dc_title}
        </h3>
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
          {t.dc_note}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="min-w-[240px] flex-[1_1_320px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.dc_token_label}</span>
          <input
            dir="ltr"
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={row.bot_token_preview ?? ""}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
          <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
            {row.bot_token_preview ? t.tg_token_keep : t.dc_token_help}
          </span>
        </label>
        <button
          type="button"
          disabled={busy || token.trim() === ""}
          onClick={() =>
            void act(async () => {
              await saveDiscordChannel({ bot_token: token.trim() });
              setToken("");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? t.wc_saving : t.tg_token_save}
        </button>
      </div>

      <div className="border-od-border mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button
          type="button"
          disabled={busy || row.bot_token_preview === null}
          onClick={() =>
            void act(async () => {
              const answer = await testDiscordChannel();
              setTested(answer.bot_username ?? "");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.tg_test}
        </button>
        <button
          type="button"
          disabled={busy || row.bot_token_preview === null}
          onClick={() => void act(() => saveDiscordChannel({ enabled: !row.enabled }))}
          className="border-od-stroke bg-od-raise-10 text-od-text cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.dc_enabled}
          {row.enabled ? " ✓" : ""}
        </button>
        {row.bot_token_preview !== null ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => saveDiscordChannel({ bot_token: "" }))}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[12.5px]"
          >
            {t.tg_token_clear}
          </button>
        ) : null}
        <span className="text-od-faint ms-auto max-w-[44ch] text-[12.5px] text-pretty">
          {tested !== null
            ? interpolate(t.dc_known_bot, { bot: tested || "?" })
            : row.bot_username
              ? interpolate(t.dc_known_bot, { bot: row.bot_username })
              : row.enabled
                ? t.dc_listening_note
                : t.dc_enabled_off_help}
        </span>
      </div>

      {problem !== null ? (
        <div className="mt-3 max-w-[62ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
          {problem}
        </div>
      ) : saved ? (
        <div className="text-od-muted-5 mt-3 text-[13px]">{t.wc_saved}</div>
      ) : null}
    </div>
  );
}

/**
 * The Slack card — the pair contract without a callback: Socket Mode means nothing
 * to paste on Slack's side beyond the two tokens minted in the customer's own app.
 */
function SlackCard({ t }: { t: SettingsDictionary }) {
  const channel = useResource<SlackChannel>(() => slackChannel(), []);
  const [appToken, setAppToken] = useState("");
  const [botToken, setBotToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const row = channel.data;

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "credentials_incomplete") return t.sl_error_incomplete;
      if (thrown.code === "encryption_key_missing") return t.wc_error_no_key;
      if (thrown.code === "slack_refused") return t.sl_error_refused;
      return thrown.message;
    }
    return String(thrown);
  };

  const act = async (run: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    setTested(null);
    try {
      await run();
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  if (row === null) return null;

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.sl_title}
        </h3>
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
          {t.sl_note}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        <label className="min-w-[220px] flex-[1_1_280px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.sl_app_token}</span>
          <input
            dir="ltr"
            type="password"
            value={appToken}
            onChange={(event) => setAppToken(event.target.value)}
            placeholder={row.app_token_preview ?? "xapp-…"}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
        </label>
        <label className="min-w-[220px] flex-[1_1_280px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.sl_bot_token}</span>
          <input
            dir="ltr"
            type="password"
            value={botToken}
            onChange={(event) => setBotToken(event.target.value)}
            placeholder={row.bot_token_preview ?? "xoxb-…"}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
          <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
            {row.app_token_preview ? t.wa_secrets_keep : t.sl_secrets_help}
          </span>
        </label>
      </div>

      <div className="border-od-border mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button
          type="button"
          disabled={busy || appToken.trim() === "" || botToken.trim() === ""}
          onClick={() =>
            void act(async () => {
              await saveSlackChannel({
                app_token: appToken.trim(),
                bot_token: botToken.trim(),
              });
              setAppToken("");
              setBotToken("");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? t.wc_saving : t.sl_save}
        </button>
        <button
          type="button"
          disabled={busy || row.bot_token_preview === null}
          onClick={() =>
            void act(async () => {
              const answer = await testSlackChannel();
              setTested(answer.team_name ?? "");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.tg_test}
        </button>
        <button
          type="button"
          disabled={busy || row.bot_token_preview === null}
          onClick={() => void act(() => saveSlackChannel({ enabled: !row.enabled }))}
          className="border-od-stroke bg-od-raise-10 text-od-text cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.sl_enabled}
          {row.enabled ? " ✓" : ""}
        </button>
        {row.bot_token_preview !== null ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => saveSlackChannel({ app_token: "", bot_token: "" }))}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[12.5px]"
          >
            {t.wa_secrets_clear}
          </button>
        ) : null}
        <span className="text-od-faint ms-auto max-w-[44ch] text-[12.5px] text-pretty">
          {tested !== null
            ? interpolate(t.sl_test_ok, { team: tested || "?" })
            : row.team_name
              ? interpolate(t.sl_test_ok, { team: row.team_name })
              : row.enabled
                ? t.sl_listening_note
                : t.sl_enabled_off_help}
        </span>
      </div>

      {problem !== null ? (
        <div className="mt-3 max-w-[62ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
          {problem}
        </div>
      ) : saved ? (
        <div className="text-od-muted-5 mt-3 text-[13px]">{t.wc_saved}</div>
      ) : null}
    </div>
  );
}

/**
 * Messenger and Instagram — one card component applied twice, because the contract
 * is one contract (the WhatsApp card's), and only the words and which id the
 * customer pastes differ: a page id, or the Instagram Business account id linked to
 * a page. Both message through the page access token from the same Meta application.
 */
function MetaChatCard({
  t,
  kind,
  words,
}: {
  t: SettingsDictionary;
  kind: MetaChatKind;
  words: { title: string; note: string; accountId: string; knownAs: string };
}) {
  const channel = useResource<MetaChatChannel>(() => metaChatChannel(kind), [kind]);
  const [accountId, setAccountId] = useState<string | null>(null);
  const [accessToken, setAccessToken] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const row = channel.data;

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "credentials_incomplete") return t.mc_error_incomplete;
      if (thrown.code === "encryption_key_missing") return t.wc_error_no_key;
      if (thrown.code === "meta_refused") return t.wa_error_refused;
      return thrown.message;
    }
    return String(thrown);
  };

  const act = async (run: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    setTested(null);
    try {
      await run();
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  if (row === null) return null;

  const copyable = (label: string, value: string, key: string) => (
    <div className="min-w-[240px] flex-[1_1_320px]">
      <span className="text-od-text-3 text-[13px] font-medium">{label}</span>
      <div className="mt-2 flex flex-wrap items-start gap-2">
        <code
          dir="ltr"
          className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 min-w-[200px] flex-[1_1_240px] rounded-[7px] border p-[9px_11px] text-start text-[12.5px] [overflow-wrap:anywhere]"
        >
          {value}
        </code>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(key);
            } catch {
              setCopied(null);
            }
          }}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_12px] text-[12.5px] font-medium whitespace-nowrap"
        >
          {copied === key ? t.wc_copied : t.wc_copy}
        </button>
      </div>
    </div>
  );

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {words.title}
        </h3>
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
          {words.note}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        <label className="min-w-[200px] flex-[1_1_240px]">
          <span className="text-od-text-3 text-[13px] font-medium">{words.accountId}</span>
          <input
            dir="ltr"
            value={accountId ?? row.account_id ?? ""}
            onChange={(event) => setAccountId(event.target.value)}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
        </label>
        <label className="min-w-[220px] flex-[1_1_280px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.mc_access_token}</span>
          <input
            dir="ltr"
            type="password"
            value={accessToken}
            onChange={(event) => setAccessToken(event.target.value)}
            placeholder={row.access_token_preview ?? ""}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
        </label>
        <label className="min-w-[220px] flex-[1_1_280px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.wa_app_secret}</span>
          <input
            dir="ltr"
            type="password"
            value={appSecret}
            onChange={(event) => setAppSecret(event.target.value)}
            placeholder={row.app_secret_preview ?? ""}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
          <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
            {row.access_token_preview ? t.wa_secrets_keep : t.mc_secrets_help}
          </span>
        </label>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        {copyable(t.wa_callback_url, row.callback_url, "url")}
        {copyable(t.wa_verify_token, row.verify_token, "token")}
      </div>
      <p className="text-od-faint mt-[8px] mb-0 max-w-[72ch] text-[12.5px] text-pretty">
        {t.mc_webhook_note}
      </p>

      <div className="border-od-border mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void act(async () => {
              const token = accessToken.trim();
              const secret = appSecret.trim();
              await saveMetaChatChannel(kind, {
                account_id: (accountId ?? row.account_id ?? "").trim(),
                ...(token && secret ? { access_token: token, app_secret: secret } : {}),
              });
              setAccessToken("");
              setAppSecret("");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? t.wc_saving : t.wa_save}
        </button>
        <button
          type="button"
          disabled={busy || row.access_token_preview === null}
          onClick={() =>
            void act(async () => {
              const answer = await testMetaChatChannel(kind);
              setTested(answer.account_name ?? "");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.wa_test}
        </button>
        <button
          type="button"
          disabled={busy || row.access_token_preview === null}
          onClick={() => void act(() => saveMetaChatChannel(kind, { enabled: !row.enabled }))}
          className="border-od-stroke bg-od-raise-10 text-od-text cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.mc_enabled}
          {row.enabled ? " ✓" : ""}
        </button>
        {row.access_token_preview !== null ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(() => saveMetaChatChannel(kind, { access_token: "", app_secret: "" }))
            }
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[12.5px]"
          >
            {t.wa_secrets_clear}
          </button>
        ) : null}
        <span className="text-od-faint ms-auto max-w-[44ch] text-[12.5px] text-pretty">
          {tested !== null
            ? interpolate(words.knownAs, { name: tested || "?" })
            : row.account_name
              ? interpolate(words.knownAs, { name: row.account_name })
              : row.enabled
                ? t.wa_listening_note
                : t.mc_enabled_off_help}
        </span>
      </div>

      {problem !== null ? (
        <div className="mt-3 max-w-[62ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
          {problem}
        </div>
      ) : saved ? (
        <div className="text-od-muted-5 mt-3 text-[13px]">{t.wc_saved}</div>
      ) : null}
    </div>
  );
}

/**
 * The WhatsApp card — the first platform channel (§B13): the customer's own Meta
 * application, two write-only secrets travelling as a pair, and the two values Meta
 * asks for on its side — the callback address and the verify token — shown here
 * because this card is where somebody stands while filling in Meta's form.
 */
function WhatsAppCard({ t }: { t: SettingsDictionary }) {
  const channel = useResource<WhatsAppChannel>(() => whatsappChannel(), []);
  const [phoneNumberId, setPhoneNumberId] = useState<string | null>(null);
  const [accessToken, setAccessToken] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const row = channel.data;

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "credentials_incomplete") return t.wa_error_incomplete;
      if (thrown.code === "encryption_key_missing") return t.wc_error_no_key;
      if (thrown.code === "meta_refused") return t.wa_error_refused;
      return thrown.message;
    }
    return String(thrown);
  };

  const act = async (run: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    setTested(null);
    try {
      await run();
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  if (row === null) return null;

  const copyable = (label: string, value: string, key: string) => (
    <div className="min-w-[240px] flex-[1_1_320px]">
      <span className="text-od-text-3 text-[13px] font-medium">{label}</span>
      <div className="mt-2 flex flex-wrap items-start gap-2">
        <code
          dir="ltr"
          className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 min-w-[200px] flex-[1_1_240px] rounded-[7px] border p-[9px_11px] text-start text-[12.5px] [overflow-wrap:anywhere]"
        >
          {value}
        </code>
        <button
          type="button"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(key);
            } catch {
              setCopied(null);
            }
          }}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_12px] text-[12.5px] font-medium whitespace-nowrap"
        >
          {copied === key ? t.wc_copied : t.wc_copy}
        </button>
      </div>
    </div>
  );

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.wa_title}
        </h3>
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
          {t.wa_note}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        <label className="min-w-[200px] flex-[1_1_240px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.wa_phone_number_id}</span>
          <input
            dir="ltr"
            value={phoneNumberId ?? row.phone_number_id ?? ""}
            onChange={(event) => setPhoneNumberId(event.target.value)}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
        </label>
        <label className="min-w-[220px] flex-[1_1_280px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.wa_access_token}</span>
          <input
            dir="ltr"
            type="password"
            value={accessToken}
            onChange={(event) => setAccessToken(event.target.value)}
            placeholder={row.access_token_preview ?? ""}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
        </label>
        <label className="min-w-[220px] flex-[1_1_280px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.wa_app_secret}</span>
          <input
            dir="ltr"
            type="password"
            value={appSecret}
            onChange={(event) => setAppSecret(event.target.value)}
            placeholder={row.app_secret_preview ?? ""}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
          <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
            {row.access_token_preview ? t.wa_secrets_keep : t.wa_secrets_help}
          </span>
        </label>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        {copyable(t.wa_callback_url, row.callback_url, "url")}
        {copyable(t.wa_verify_token, row.verify_token, "token")}
      </div>
      <p className="text-od-faint mt-[8px] mb-0 max-w-[72ch] text-[12.5px] text-pretty">
        {t.wa_webhook_note}
      </p>

      <div className="border-od-border mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            void act(async () => {
              const token = accessToken.trim();
              const secret = appSecret.trim();
              await saveWhatsAppChannel({
                phone_number_id: (phoneNumberId ?? row.phone_number_id ?? "").trim(),
                // The pair travels together or not at all - the server refuses half.
                ...(token && secret ? { access_token: token, app_secret: secret } : {}),
              });
              setAccessToken("");
              setAppSecret("");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? t.wc_saving : t.wa_save}
        </button>
        <button
          type="button"
          disabled={busy || row.access_token_preview === null}
          onClick={() =>
            void act(async () => {
              const answer = await testWhatsAppChannel();
              setTested(answer.display_phone_number ?? "");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.wa_test}
        </button>
        <button
          type="button"
          disabled={busy || row.access_token_preview === null}
          onClick={() => void act(() => saveWhatsAppChannel({ enabled: !row.enabled }))}
          className="border-od-stroke bg-od-raise-10 text-od-text cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.wa_enabled}
          {row.enabled ? " ✓" : ""}
        </button>
        {row.access_token_preview !== null ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void act(() => saveWhatsAppChannel({ access_token: "", app_secret: "" }))
            }
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[12.5px]"
          >
            {t.wa_secrets_clear}
          </button>
        ) : null}
        <span className="text-od-faint ms-auto max-w-[44ch] text-[12.5px] text-pretty">
          {tested !== null
            ? interpolate(t.wa_test_ok, { number: tested || "?" })
            : row.verified_name
              ? interpolate(t.wa_known_number, { name: row.verified_name })
              : row.enabled
                ? t.wa_listening_note
                : t.wa_enabled_off_help}
        </span>
      </div>

      {problem !== null ? (
        <div className="mt-3 max-w-[62ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
          {problem}
        </div>
      ) : saved ? (
        <div className="text-od-muted-5 mt-3 text-[13px]">{t.wc_saved}</div>
      ) : null}
    </div>
  );
}

/**
 * The email card — §B13's third no-platform channel: an IMAP/SMTP mailbox the
 * customer already owns.
 *
 * Deliberately not the notifications tab's SMTP: that one is how Tel-Agent talks to
 * its operator, this one is how the business talks to its customers, per workspace,
 * on credentials the customer owns (§B9.2). The password follows the cards'
 * contract — masked once saved, the echoed mask never treated as an edit, an empty
 * write removing it and switching the channel off with it.
 */
function EmailCard({ t }: { t: SettingsDictionary }) {
  const channel = useResource<EmailChannel>(() => emailChannel(), []);
  // The saved value until somebody types; then what they typed. `null` is
  // "untouched", which is why these are not initialised from the row in an effect.
  const [fields, setFields] = useState<Record<string, string | null>>({});
  const [flags, setFlags] = useState<Record<string, boolean | null>>({});
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const row = channel.data;

  const text = (key: keyof EmailChannel): string =>
    fields[key] ?? String(row?.[key] ?? "");
  const flag = (key: keyof EmailChannel): boolean =>
    flags[key] ?? Boolean(row?.[key] ?? false);

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "mailbox_incomplete") return t.em_error_incomplete;
      if (thrown.code === "encryption_key_missing") return t.wc_error_no_key;
      if (thrown.code === "mailbox_refused") return thrown.message;
      return thrown.message;
    }
    return String(thrown);
  };

  const act = async (run: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    setTested(false);
    try {
      await run();
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  const save = (extra: Partial<Parameters<typeof saveEmailChannel>[0]> = {}) =>
    act(async () => {
      await saveEmailChannel({
        imap_host: text("imap_host"),
        imap_port: Number(text("imap_port")) || 993,
        smtp_host: text("smtp_host"),
        smtp_port: Number(text("smtp_port")) || 587,
        username: text("username"),
        from_address: text("from_address"),
        imap_ssl: flag("imap_ssl"),
        smtp_tls: flag("smtp_tls"),
        smtp_ssl: flag("smtp_ssl"),
        // Omitted when untouched: the server ignores an echoed mask, and not
        // sending one is the half that does not rely on it doing so.
        ...(password === "" ? {} : { password }),
        ...extra,
      });
      setPassword("");
    });

  if (row === null) return null;

  const box = (
    key: keyof EmailChannel,
    label: string,
    options: { placeholder?: string; wide?: boolean; secret?: boolean } = {},
  ) => (
    <label className={options.wide ? "min-w-[220px] flex-[2_1_260px]" : "min-w-[110px] flex-[1_1_120px]"}>
      <span className="text-od-text-3 text-[13px] font-medium">{label}</span>
      <input
        dir="ltr"
        value={text(key)}
        placeholder={options.placeholder}
        onChange={(event) => setFields((all) => ({ ...all, [key]: event.target.value }))}
        className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
      />
    </label>
  );

  const tick = (key: keyof EmailChannel, label: string) => (
    <label className="text-od-text-3 flex cursor-pointer items-center gap-2 text-[13px]">
      <input
        type="checkbox"
        checked={flag(key)}
        onChange={(event) => setFlags((all) => ({ ...all, [key]: event.target.checked }))}
      />
      {label}
    </label>
  );

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.em_title}
        </h3>
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
          {t.em_note}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        {box("imap_host", t.em_imap_host, { placeholder: "imap.example.com", wide: true })}
        {box("imap_port", t.em_imap_port)}
        {box("smtp_host", t.em_smtp_host, { placeholder: "smtp.example.com", wide: true })}
        {box("smtp_port", t.em_smtp_port)}
      </div>
      <div className="mt-3 flex flex-wrap gap-3">
        {box("username", t.em_username, { wide: true })}
        <label className="min-w-[220px] flex-[2_1_260px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.em_password}</span>
          <input
            dir="ltr"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={row.password_preview ?? ""}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
          <span className="text-od-faint-2 mt-[6px] block text-[12.5px]">
            {row.password_preview ? t.em_password_keep : t.em_password_help}
          </span>
        </label>
        {box("from_address", t.em_from, { wide: true })}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
        {tick("imap_ssl", t.em_imap_ssl)}
        {tick("smtp_tls", t.em_smtp_tls)}
        {tick("smtp_ssl", t.em_smtp_ssl)}
      </div>

      <div className="border-od-border mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button
          type="button"
          disabled={busy}
          onClick={() => void save()}
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? t.wc_saving : t.em_save}
        </button>
        <button
          type="button"
          disabled={busy || row.password_preview === null}
          onClick={() =>
            void act(async () => {
              await testEmailChannel();
              setTested(true);
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.em_test}
        </button>
        <button
          type="button"
          disabled={busy || row.password_preview === null}
          onClick={() => void act(() => saveEmailChannel({ enabled: !row.enabled }))}
          className="border-od-stroke bg-od-raise-10 text-od-text cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.em_enabled}
          {row.enabled ? " ✓" : ""}
        </button>
        {row.password_preview !== null ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => saveEmailChannel({ password: "" }))}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[12.5px]"
          >
            {t.em_password_clear}
          </button>
        ) : null}
        <span className="text-od-faint ms-auto max-w-[44ch] text-[12.5px] text-pretty">
          {tested ? t.em_test_ok : row.enabled ? t.em_polling_note : t.em_enabled_off_help}
        </span>
      </div>

      {problem !== null ? (
        <div className="mt-3 max-w-[62ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
          {problem}
        </div>
      ) : saved ? (
        <div className="text-od-muted-5 mt-3 text-[13px]">{t.wc_saved}</div>
      ) : null}
    </div>
  );
}

/**
 * The Telegram card — the second channel with something real behind it (Milestone 3).
 *
 * The web chat card's contract, applied to a bot token: masked once saved, the mask
 * never echoed back as an edit, the switch refusing to turn on while there is no
 * token — and §A6.8's "Test connection", which asks Telegram `getMe` and names the
 * bot, proving the link rather than claiming it.
 */
function TelegramCard({ t }: { t: SettingsDictionary }) {
  const channel = useResource<TelegramChannel>(() => telegramChannel(), []);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tested, setTested] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const row = channel.data;

  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "no_bot_token") return t.tg_error_no_token;
      if (thrown.code === "encryption_key_missing") return t.wc_error_no_key;
      if (thrown.code === "telegram_refused") return t.tg_error_refused;
      return thrown.message;
    }
    return String(thrown);
  };

  const act = async (run: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSaved(false);
    setTested(null);
    try {
      await run();
      setSaved(true);
      channel.reload();
    } catch (thrown) {
      setProblem(describe(thrown));
    } finally {
      setBusy(false);
    }
  };

  if (row === null) return null;

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.tg_title}
        </h3>
        <span className="text-od-faint max-w-[52ch] text-[12.5px] text-pretty">
          {t.tg_note}
        </span>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="min-w-[240px] flex-[1_1_320px]">
          <span className="text-od-text-3 text-[13px] font-medium">{t.tg_token_label}</span>
          <input
            dir="ltr"
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={row.bot_token_preview ?? t.tg_token_placeholder}
            className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono mt-2 w-full rounded-[7px] border p-[9px_11px] text-start text-[13px]"
          />
          <span className="text-od-faint-2 mt-[6px] block max-w-[62ch] text-pretty text-[12.5px]">
            {row.bot_token_preview ? t.tg_token_keep : t.tg_token_help}
          </span>
        </label>
        <button
          type="button"
          disabled={busy || token.trim() === ""}
          onClick={() =>
            void act(async () => {
              await saveTelegramChannel({ bot_token: token.trim() });
              setToken("");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? t.wc_saving : t.tg_token_save}
        </button>
      </div>

      <div className="border-od-border mt-4 flex flex-wrap items-center gap-3 border-t pt-4">
        <button
          type="button"
          disabled={busy || row.bot_token_preview === null}
          onClick={() =>
            void act(async () => {
              const answer = await testTelegramChannel();
              setTested(answer.bot_username ?? "");
            })
          }
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.tg_test}
        </button>
        <button
          type="button"
          disabled={busy || row.bot_token_preview === null}
          onClick={() => void act(() => saveTelegramChannel({ enabled: !row.enabled }))}
          className="border-od-stroke bg-od-raise-10 text-od-text cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.tg_enabled}
          {row.enabled ? " ✓" : ""}
        </button>
        {row.bot_token_preview !== null ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => saveTelegramChannel({ bot_token: "" }))}
            className="border-od-line text-od-muted-4 hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[12.5px]"
          >
            {t.tg_token_clear}
          </button>
        ) : null}

        <span className="text-od-faint ms-auto max-w-[44ch] text-[12.5px] text-pretty">
          {tested !== null
            ? interpolate(t.tg_test_ok, { bot: tested ? `@${tested}` : "?" })
            : row.bot_username
              ? interpolate(t.tg_known_bot, { bot: `@${row.bot_username}` })
              : row.enabled
                ? t.tg_polling_note
                : t.tg_enabled_off_help}
        </span>
      </div>

      {problem !== null ? (
        <div className="mt-3 max-w-[62ch] text-pretty text-[13px] text-[color:var(--od-red-text)]">
          {problem}
        </div>
      ) : saved ? (
        <div className="text-od-muted-5 mt-3 text-[13px]">{t.wc_saved}</div>
      ) : null}
    </div>
  );
}

/** The date as the reader's locale writes it, not as the server stores it. */
function onDay(iso: string, locale: Locale): string {
  return new Date(iso).toLocaleDateString(locale === "ar" ? "ar" : locale, {
    day: "numeric",
    month: "short",
  });
}

/**
 * A credential, in the one moment it exists on a screen.
 *
 * Both halves of this tab mint something the server will never hand back: a machine
 * token is stored as a hash, and a webhook secret is encrypted and masked on every
 * read. So the copy happens here, while the person is already looking at it, and the
 * box says so rather than leaving them to find out on the next load.
 */
function ShownOnce({ t, value, onDone }: { t: SettingsDictionary; value: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      // Clipboard can be unavailable; the value is on screen to select by hand.
    }
  }

  return (
    <div
      className="rounded-[9px] border p-[13px_15px]"
      style={{ borderColor: "var(--od-amber-border)", background: "var(--od-amber-bg)" }}
    >
      <div className="text-[12.5px] text-pretty text-[color:var(--od-amber-text)]">
        {t.shown_once}
      </div>
      <div
        dir="ltr"
        className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[9px] rounded-[7px] border p-[10px_12px] text-[13px] [overflow-wrap:anywhere]"
      >
        {value}
      </div>
      <div className="mt-[10px] flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void copy()}
          className="cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px] font-medium"
          style={{
            borderColor: copied ? "var(--od-green-border)" : "var(--od-border-7)",
            background: copied ? "rgba(63,185,132,.10)" : "transparent",
            color: copied ? "var(--od-green-text)" : "var(--od-text-3)",
          }}
        >
          {copied ? t.wc_copied : t.wc_copy}
        </button>
        <button
          type="button"
          onClick={onDone}
          className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px]"
        >
          {t.close}
        </button>
      </div>
    </div>
  );
}

/** What each scope opens, said in words and in the path itself. */
const SCOPES: { id: MachineScope; label: Key; note: Key; path: string }[] = [
  { id: "hooks", label: "mt_hooks", note: "mt_hooks_note", path: "/hooks/…" },
  { id: "mcp", label: "mt_mcp", note: "mt_mcp_note", path: "/mcp" },
];

/** Minting one: a label to recognise it by, and which of the two paths it opens. */
function NewTokenDialog({
  t,
  onMinted,
  onClose,
}: {
  t: SettingsDictionary;
  onMinted: (token: MintedToken) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [scope, setScope] = useState<MachineScope>("hooks");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setPending(true);
    setError(null);
    try {
      onMinted(await mintToken(name.trim(), scope));
    } catch (thrown) {
      setError(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-auto p-[60px_24px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div className="border-od-border-9 bg-od-panel w-full max-w-[520px] rounded-xl border">
        <div className="border-od-line flex flex-wrap items-start justify-between gap-x-5 gap-y-3 border-b p-[20px_22px]">
          <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.mt_add}</h2>
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_10px] hover:bg-[var(--od-raise-6)]"
          >
            {t.close}
          </button>
        </div>

        <form
          className="flex flex-col gap-[18px] p-[20px_22px]"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="block">
            <span className="text-od-muted-5 text-[12.5px]">{t.mt_name_label}</span>
            <input
              autoComplete="off"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[6px] w-full rounded-[7px] border p-[10px_12px] text-[14px]"
            />
            <span className="text-od-faint mt-[6px] block text-[12px] text-pretty">
              {t.mt_name_hint}
            </span>
          </label>

          <div>
            <div className="text-od-muted-5 text-[12.5px]">{t.mt_scope_label}</div>
            <div className="mt-2 flex flex-col gap-2">
              {SCOPES.map((entry) => {
                const on = scope === entry.id;
                return (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => setScope(entry.id)}
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
                      <span className="text-od-text-2 flex flex-wrap items-baseline gap-x-[9px] font-semibold">
                        {t[entry.label]}
                        <span dir="ltr" className="mono ltr-data text-od-faint text-[12px]">
                          {entry.path}
                        </span>
                      </span>
                      <span className="text-od-muted-5 mt-[3px] block text-[12.5px] text-pretty">
                        {t[entry.note]}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {error === null ? null : (
            <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">{error}</p>
          )}

          <div className="flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_16px]"
            >
              {t.cancel}
            </button>
            <button
              type="submit"
              disabled={pending || name.trim() === ""}
              className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t.mt_create}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * The machine tokens — §B9.1's second and third credentials, over `/api/tokens`.
 *
 * What is not drawn here, and why: there is no base-URL row for a documented REST
 * surface, because that is Milestone 6 and nothing serves `/api/v1` yet. There is no
 * "last used 2 h ago" either — the server records `last_used_at` at a five-minute
 * resolution, so the day is what it can honestly say.
 */
function TokensPanel({ locale, t }: { locale: Locale; t: SettingsDictionary }) {
  const tokens = useResource(() => tokenList(), []);
  const [minting, setMinting] = useState(false);
  const [minted, setMinted] = useState<MintedToken | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      setConfirming(null);
      tokens.reload();
    } catch (thrown) {
      setNotice(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border p-[18px]">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
        <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
          {t.mt_title}
        </h3>
        <button
          type="button"
          onClick={() => {
            setMinted(null);
            setMinting(true);
          }}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium whitespace-nowrap"
        >
          {t.mt_add}
        </button>
      </div>
      <p className="text-od-faint mt-[6px] mb-0 max-w-[72ch] text-[12.5px] text-pretty">
        {t.mt_note}
      </p>

      {minted === null ? null : (
        <div className="mt-3">
          <ShownOnce t={t} value={minted.token} onDone={() => setMinted(null)} />
          <p className="text-od-faint mt-2 mb-0 text-[12px]">{t.mt_bearer}</p>
        </div>
      )}

      {notice === null ? null : (
        <p className="mt-3 mb-0 text-[13px] text-[color:var(--od-red-text-6)]">{notice}</p>
      )}

      {tokens.data === null && tokens.loading ? (
        <p className="text-od-muted-5 m-0 py-[14px] text-[13px]">{t.live_loading}</p>
      ) : tokens.data === null ? (
        <div className="py-[14px]">
          <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
            {tokens.error?.message ?? t.live_failed}
          </p>
          <button
            type="button"
            onClick={tokens.reload}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
          >
            {t.live_retry}
          </button>
        </div>
      ) : tokens.data.length === 0 ? (
        <p className="text-od-muted-5 m-0 py-[14px] text-[13px] text-pretty">{t.mt_empty}</p>
      ) : (
        <div className="mt-2 flex flex-col">
          {tokens.data.map((row) => {
            const scope = SCOPES.find((entry) => entry.id === row.scope);
            return (
              <div
                key={row.id}
                className="border-od-border flex flex-wrap items-center gap-x-4 gap-y-[10px] border-b py-3"
              >
                <div className="min-w-[200px] flex-[1_1_240px]">
                  <div className="flex flex-wrap items-baseline gap-x-[9px] gap-y-1">
                    <span className="text-od-text-3 font-medium text-pretty">{row.name}</span>
                    {/* Which door it opens, in the path's own characters. */}
                    <span
                      dir="ltr"
                      className="border-od-border-7 text-od-muted-5 mono ltr-data rounded-[5px] border bg-[var(--od-raise-5)] p-[1px_7px] text-[11.5px]"
                    >
                      {scope?.path ?? row.scope}
                    </span>
                  </div>
                  {/* Only the last four are ever shown — the rest is a hash on the server. */}
                  <div
                    dir="ltr"
                    className="mono ltr-data text-od-muted-5 mt-[3px] text-start text-[12.5px]"
                  >
                    {`••••${row.last_four}`}
                  </div>
                </div>
                <span className="text-od-muted-5 text-[12.5px]">
                  {row.last_used_at === null
                    ? t.mt_unused
                    : interpolate(t.mt_used, { when: onDay(row.last_used_at, locale) })}
                </span>
                <div className="ms-auto flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void act(async () => {
                        setMinted(await rotateToken(row.id));
                      })
                    }
                    className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px] hover:bg-[var(--od-raise-4)] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {t.mt_rotate}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      confirming === row.id
                        ? void act(() => removeToken(row.id))
                        : setConfirming(row.id)
                    }
                    className="cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px] disabled:cursor-not-allowed disabled:opacity-50"
                    style={{
                      borderColor:
                        confirming === row.id ? "var(--od-red-border-2)" : "var(--od-border-7)",
                      color:
                        confirming === row.id ? "var(--od-red-text-4)" : "var(--od-muted)",
                    }}
                  >
                    {confirming === row.id ? t.u_confirm_remove : t.remove}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {minting ? (
        <NewTokenDialog
          t={t}
          onMinted={(token) => {
            setMinted(token);
            setMinting(false);
            tokens.reload();
          }}
          onClose={() => setMinting(false)}
        />
      ) : null}
    </div>
  );
}

/** Adding one: where to post, and which events it is told about. */
function NewWebhookDialog({
  t,
  events,
  onAdded,
  onClose,
}: {
  t: SettingsDictionary;
  events: string[];
  onAdded: (created: WebhookWithSecret) => void;
  onClose: () => void;
}) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setPending(true);
    setError(null);
    try {
      onAdded(await addWebhook({ url: url.trim(), events: chosen, name: name.trim() || null }));
    } catch (thrown) {
      if (thrown instanceof ApiError && thrown.code === "invalid_url") setError(t.w_bad_url);
      else if (thrown instanceof ApiError && thrown.code === "no_events") setError(t.w_no_events);
      else setError(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setPending(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-auto p-[60px_24px]"
      style={{ background: "var(--od-scrim-3)" }}
    >
      <div className="border-od-border-9 bg-od-panel w-full max-w-[520px] rounded-xl border">
        <div className="border-od-line flex flex-wrap items-start justify-between gap-x-5 gap-y-3 border-b p-[20px_22px]">
          <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.w_add}</h2>
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_10px] hover:bg-[var(--od-raise-6)]"
          >
            {t.close}
          </button>
        </div>

        <form
          className="flex flex-col gap-[18px] p-[20px_22px]"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label className="block">
            <span className="text-od-muted-5 text-[12.5px]">{t.w_url_label}</span>
            <input
              dir="ltr"
              autoComplete="off"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              maxLength={500}
              className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[6px] w-full rounded-[7px] border p-[10px_12px] text-[13.5px]"
            />
          </label>

          <label className="block">
            <span className="text-od-muted-5 text-[12.5px]">{t.w_name_label}</span>
            <input
              autoComplete="off"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mt-[6px] w-full rounded-[7px] border p-[10px_12px] text-[14px]"
            />
          </label>

          <div>
            <div className="text-od-muted-5 text-[12.5px]">{t.w_events_label}</div>
            <div className="mt-2 flex flex-col gap-[6px]">
              {events.map((event) => {
                const on = chosen.includes(event);
                const label = WEBHOOK_EVENT_LABEL[event];
                return (
                  <button
                    key={event}
                    type="button"
                    onClick={() =>
                      setChosen(
                        on ? chosen.filter((entry) => entry !== event) : [...chosen, event],
                      )
                    }
                    className="flex cursor-pointer items-center gap-[10px] rounded-[8px] border p-[9px_12px] text-start"
                    style={{
                      borderColor: on ? "var(--od-violet)" : "var(--od-border-7)",
                      background: on ? "var(--od-raise-10)" : "var(--od-canvas-2)",
                    }}
                  >
                    <span
                      className="size-[14px] flex-none rounded-[4px] border"
                      style={{
                        borderColor: on ? "var(--od-violet)" : "var(--od-stroke-5)",
                        background: on ? "var(--od-violet)" : "transparent",
                      }}
                    />
                    <span className="min-w-0 flex-1">
                      {/* The sentence when the screen knows the name, and the name
                          itself when it does not — a vocabulary entry added on the
                          server degrades to machine output, never to a blank row. */}
                      <span className="text-od-text-3 block text-[13.5px]">
                        {label ? t[label] : event}
                      </span>
                      <span
                        dir="ltr"
                        className="mono ltr-data text-od-faint block text-start text-[11.5px]"
                      >
                        {event}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {error === null ? null : (
            <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">{error}</p>
          )}

          <div className="flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_16px]"
            >
              {t.cancel}
            </button>
            <button
              type="submit"
              disabled={pending || url.trim() === "" || chosen.length === 0}
              className="border-od-stroke bg-od-raise-10 text-od-text hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_16px] text-[13.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t.w_add}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/**
 * The webhook registry, over `/api/webhooks`.
 *
 * What went, and why: the Healthy / Failing chip, the "delivered 09:44" line and the
 * replay note all described a delivery record that does not exist. §B5's table holds
 * what a webhook *is*; what happened to one delivery rides on the background job that
 * sent it, and there is no endpoint reporting it per hook. A control with no endpoint
 * is removed, not drawn.
 */
function WebhooksPanel({ t }: { t: SettingsDictionary }) {
  const hooks = useResource(() => webhooksList(), []);
  const events = useResource(() => webhookEvents(), []);
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      setConfirming(null);
      hooks.reload();
    } catch (thrown) {
      setNotice(thrown instanceof Error ? thrown.message : String(thrown));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="border-od-line bg-od-panel-deep-3 rounded-[10px] border">
      <div className="flex flex-wrap items-start justify-between gap-x-[18px] gap-y-3 p-[18px_18px_12px]">
        <div className="min-w-0 flex-[1_1_300px]">
          <h3 className="text-od-muted-4 m-0 text-[13px] font-semibold tracking-[.07em] uppercase">
            {t.w_title}
          </h3>
          <p className="text-od-faint mt-[6px] mb-0 max-w-[70ch] text-[12.5px] text-pretty">
            {t.w_note}
          </p>
        </div>
        <button
          type="button"
          disabled={events.data === null}
          onClick={() => {
            setSecret(null);
            setAdding(true);
          }}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[8px_14px] text-[13px] font-medium whitespace-nowrap disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t.w_add}
        </button>
      </div>

      {secret === null ? null : (
        <div className="p-[0_18px_14px]">
          <ShownOnce t={t} value={secret} onDone={() => setSecret(null)} />
        </div>
      )}

      {notice === null ? null : (
        <p className="m-0 p-[0_18px_14px] text-[13px] text-[color:var(--od-red-text-6)]">
          {notice}
        </p>
      )}

      {hooks.data === null && hooks.loading ? (
        <p className="text-od-muted-5 m-0 p-[0_18px_18px] text-[13px]">{t.live_loading}</p>
      ) : hooks.data === null ? (
        <div className="p-[0_18px_18px]">
          <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
            {hooks.error?.message ?? t.live_failed}
          </p>
          <button
            type="button"
            onClick={hooks.reload}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
          >
            {t.live_retry}
          </button>
        </div>
      ) : hooks.data.length === 0 ? (
        <p className="text-od-muted-5 m-0 p-[0_18px_18px] text-[13px]">{t.w_empty}</p>
      ) : (
        hooks.data.map((hook) => (
          <div
            key={hook.id}
            className="flex flex-wrap items-start gap-x-4 gap-y-[10px] border-t border-[color:var(--od-raise-6)] p-[14px_18px]"
          >
            <div className="min-w-[220px] flex-[1_1_260px]">
              <div className="flex flex-wrap items-baseline gap-x-[9px] gap-y-1">
                {hook.name ? (
                  <span className="text-od-text-3 font-medium text-pretty">{hook.name}</span>
                ) : null}
                <span
                  className="rounded-[5px] border p-[2px_9px] text-[11.5px] font-semibold whitespace-nowrap"
                  style={{
                    borderColor: hook.enabled ? "var(--od-green-border)" : "var(--od-border-7)",
                    background: hook.enabled ? "rgba(63,185,132,.10)" : "var(--od-raise-5)",
                    color: hook.enabled ? "var(--od-green-text)" : "var(--od-muted-5)",
                  }}
                >
                  {hook.enabled ? t.w_on : t.w_off}
                </span>
              </div>
              <div
                dir="ltr"
                className="mono ltr-data text-od-text-3 mt-1 text-start text-[13px] [overflow-wrap:anywhere]"
              >
                {hook.url}
              </div>
              <div
                dir="ltr"
                className="mono ltr-data text-od-muted-5 mt-1 text-start text-[12.5px] [overflow-wrap:anywhere]"
              >
                {hook.events.join(" · ")}
              </div>
              <div className="text-od-faint mt-[6px] flex flex-wrap items-baseline gap-x-2 text-[12px]">
                <span>{t.w_secret_label}</span>
                <span dir="ltr" className="mono ltr-data">
                  {hook.secret_preview}
                </span>
              </div>
            </div>

            <div className="ms-auto flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void act(() => changeWebhook(hook.id, { enabled: !hook.enabled }))}
                className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px] hover:bg-[var(--od-raise-4)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {hook.enabled ? t.w_disable : t.w_enable}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    setSecret((await rotateWebhookSecret(hook.id)).secret);
                  })
                }
                className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px] hover:bg-[var(--od-raise-4)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {t.w_rotate}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  confirming === hook.id
                    ? void act(() => removeWebhook(hook.id))
                    : setConfirming(hook.id)
                }
                className="cursor-pointer rounded-md border bg-transparent p-[7px_12px] text-[13px] disabled:cursor-not-allowed disabled:opacity-50"
                style={{
                  borderColor:
                    confirming === hook.id ? "var(--od-red-border-2)" : "var(--od-border-7)",
                  color: confirming === hook.id ? "var(--od-red-text-4)" : "var(--od-muted)",
                }}
              >
                {confirming === hook.id ? t.u_confirm_remove : t.remove}
              </button>
            </div>
          </div>
        ))
      )}

      {adding && events.data !== null ? (
        <NewWebhookDialog
          t={t}
          events={events.data}
          onAdded={(created) => {
            setSecret(created.secret);
            setAdding(false);
            hooks.reload();
          }}
          onClose={() => setAdding(false)}
        />
      ) : null}
    </div>
  );
}

/**
 * The API tab, wired.
 *
 * Two panels, and until #123 neither had a backend: the machine tokens over
 * `/api/tokens` (§B9.1) and the webhook registry over `/api/webhooks` (§B5). Both
 * mint a credential the server will never hand back, which is why `ShownOnce` exists
 * once above rather than twice below.
 */
function ApiPanels({ locale, t }: { locale: Locale; t: SettingsDictionary }) {
  return (
    <div className="flex flex-col gap-4">
      <TokensPanel locale={locale} t={t} />
      <WebhooksPanel t={t} />
    </div>
  );
}

/**
 * The MCP tab, wired to the endpoint Milestone 7 built (`api/routes/mcp.py`).
 *
 * What the drawing promised that the server cannot keep is gone, per the pattern the
 * API tab set (#124): the enable switch (the endpoint is live exactly when a token
 * with the `mcp` scope exists — there is nothing else to switch), the per-tool
 * switches (v1 serves its five tools to any holder of the token), the invented
 * `.local` address, and the three phone-era tools nothing serves. The tool list is
 * `OUR_TOOLS`, kept in the same order as the server's own registry.
 */
function McpPanels({
  locale,
  t,
  onOpenApiTab,
}: {
  locale: Locale;
  t: SettingsDictionary;
  onOpenApiTab: () => void;
}) {
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
        </div>

        <div className="p-[0_18px_14px]">
          <div
            dir="ltr"
            className="mono ltr-data border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-[7px] border p-[11px_13px] text-[12.5px] [overflow-wrap:anywhere]"
          >
            {`${API_URL}/mcp`}
          </div>
          <div className="text-od-muted-5 mt-[9px] text-[12.5px] text-pretty">
            {t.m_token_note}{" "}
            <button
              type="button"
              onClick={onOpenApiTab}
              className="cursor-pointer border-0 bg-transparent p-0 text-[12.5px] text-[color:var(--od-violet-3)] hover:underline"
            >
              {t.m_token_link}
            </button>
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
                  tool.scope === "read" ? "var(--od-border-7)" : "var(--od-violet-border)",
                background:
                  tool.scope === "read" ? "var(--od-raise-5)" : "rgba(139,124,255,.12)",
                color: tool.scope === "read" ? "var(--od-muted-5)" : "var(--od-violet-3)",
              }}
            >
              {t[`scope_${tool.scope}`]}
            </span>
          </div>
        ))}

        {/* What holding the token means, said plainly. */}
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
