/**
 * The settings tabs, in the order they appear. Some are links to their own screen.
 *
 * Every piece of prose is a key into `locales/<lang>/settings.json`. What stays a
 * literal here is data: hostnames, ports, addresses, key prefixes, event names,
 * language endonyms, and the names and emails of the people in the workspace.
 */

import type { SettingsDictionary } from "@/app/[locale]/settings/page";

export type Key = keyof SettingsDictionary;

export const TABS: { id: string; label: Key; href?: string }[] = [
  { id: "profile", label: "tab_profile" },
  { id: "general", label: "tab_general" },
  { id: "numbers", label: "tab_numbers", href: "/numbers" },
  { id: "catalogue", label: "tab_catalogue", href: "/catalogue" },
  { id: "users", label: "tab_users" },
  { id: "recording", label: "tab_recording" },
  { id: "notifications", label: "tab_notifications" },
  { id: "integrations", label: "tab_integrations" },
  { id: "api", label: "tab_api" },
  { id: "mcp", label: "tab_mcp" },
  { id: "advanced", label: "tab_advanced" },
];

export const PAGE_LINKS: { id: string; label: Key; href: string }[] = [
  { id: "numbers", label: "link_numbers", href: "/numbers" },
  { id: "catalogue", label: "link_catalogue", href: "/catalogue" },
  { id: "rules", label: "link_rules", href: "/rules" },
  { id: "apps", label: "link_apps", href: "/apps" },
  { id: "connectors", label: "link_connectors", href: "/connectors" },
  { id: "usage", label: "link_usage", href: "/usage" },
  { id: "health", label: "link_health", href: "/health" },
  { id: "backup", label: "link_backup", href: "/backup" },
  { id: "update", label: "link_update", href: "/update" },
];

/**
 * A field's shown value is either machine-readable (an address, a port range, a
 * language's own name) or a phrase. Only the first kind is monospaced and forced
 * left-to-right.
 */
export type Control =
  | { kind: "input"; value?: Key; valueText?: string; mono?: boolean; invalid?: boolean }
  | { kind: "select"; value?: Key; valueText?: string }
  | { kind: "switch"; on: boolean };

export type Field = { id: string; label: Key; help?: Key; control: Control };

export type Section = { title: Key; body: Key; fields: Field[] };

export const SECTIONS: Record<string, Section> = {
  // No fields: the identity card and the language panel are wired, the name field
  // had no column behind it, and editing the email needs the current password (it
  // is where reset codes go) - that confirmation flow is its own future piece.
  profile: { title: "sec_profile_title", body: "sec_profile_body", fields: [] },
  general: {
    title: "sec_general_title",
    body: "sec_general_body",
    fields: [
      {
        id: "instname",
        label: "f_instname",
        help: "f_instname_help",
        control: { kind: "input", valueText: "Wagner & Partner" },
      },
      {
        id: "lang",
        label: "f_lang",
        help: "f_lang_help_general",
        control: { kind: "select", valueText: "Deutsch (Österreich)" },
      },
      {
        id: "tz",
        label: "f_tz",
        help: "f_tz_help",
        control: { kind: "select", valueText: "Europe/Vienna" },
      },
      { id: "dateformat", label: "f_dateformat", control: { kind: "select", value: "v_dateformat" } },
    ],
  },
  recording: {
    title: "sec_recording_title",
    body: "sec_recording_body",
    fields: [
      { id: "record", label: "f_record", help: "f_record_help", control: { kind: "switch", on: true } },
      {
        id: "keepaudio",
        label: "f_keep_audio",
        help: "f_keep_audio_help",
        control: { kind: "select", value: "v_90_days" },
      },
      {
        id: "keeptr",
        label: "f_keep_tr",
        help: "f_keep_tr_help",
        control: { kind: "select", value: "v_12_months" },
      },
      { id: "redact", label: "f_redact", help: "f_redact_help", control: { kind: "switch", on: true } },
    ],
  },
  notifications: {
    title: "sec_notifications_title",
    body: "sec_notifications_body",
    fields: [
      {
        id: "email",
        label: "f_notify_email",
        control: { kind: "input", valueText: "office@wagner-partner.at" },
      },
      {
        id: "toolfailed",
        label: "f_tool_failed",
        help: "f_tool_failed_help",
        control: { kind: "switch", on: true },
      },
      { id: "linelost", label: "f_line_lost", control: { kind: "switch", on: true } },
      { id: "daily", label: "f_daily", help: "f_daily_help", control: { kind: "switch", on: false } },
    ],
  },
  // No fields: the team list is wired and the old two-factor switch was a drawing
  // of an enforcement that exists nowhere.
  users: { title: "sec_users_title", body: "sec_users_body", fields: [] },
  api: { title: "sec_api_title", body: "sec_api_body", fields: [] },
  mcp: { title: "sec_mcp_title", body: "sec_mcp_body", fields: [] },
  integrations: {
    title: "sec_integrations_title",
    body: "sec_integrations_body",
    fields: [
      {
        id: "calendar",
        label: "f_calendar",
        help: "f_calendar_help",
        control: { kind: "select", valueText: "CalDAV — wagner-partner.at" },
      },
      {
        id: "smsgw",
        label: "f_sms_gw",
        help: "f_sms_gw_help",
        control: { kind: "input", valueText: "sms77.io", mono: true, invalid: true },
      },
    ],
  },
  advanced: {
    title: "sec_advanced_title",
    body: "sec_advanced_body",
    fields: [
      { id: "loglevel", label: "f_loglevel", control: { kind: "select", valueText: "info" } },
      {
        id: "endpoint",
        label: "f_model_endpoint",
        control: { kind: "input", valueText: "http://localhost:8080/v1", mono: true },
      },
      {
        id: "codec",
        label: "f_codec",
        control: { kind: "input", valueText: "opus, PCMA, PCMU", mono: true },
      },
      {
        id: "experimental",
        label: "f_experimental",
        help: "f_experimental_help",
        control: { kind: "switch", on: false },
      },
    ],
  },
};

export const HOST_FIELDS: Field[] = [
  {
    id: "hostname",
    label: "f_hostname",
    help: "f_hostname_help",
    control: { kind: "input", valueText: "telagent.wagner-partner.local", mono: true },
  },
  {
    id: "publicurl",
    label: "f_public_url",
    help: "f_public_url_help",
    control: { kind: "input", valueText: "https://telagent.wagner-partner.local", mono: true },
  },
  {
    id: "tls",
    label: "f_tls",
    help: "f_tls_help",
    control: { kind: "select", value: "v_tls" },
  },
  { id: "proxy", label: "f_proxy", help: "f_proxy_help", control: { kind: "switch", on: true } },
  {
    id: "ports",
    label: "f_ports",
    help: "f_ports_help",
    control: { kind: "input", valueText: "8443 · 5061 · 16384–16584", mono: true },
  },
];

/** The visible part of a key is real; the rest is never sent to the client. */
export const API_KEYS: { id: string; name: Key; key: string; used: Key }[] = [
  { id: "website", name: "k_website", key: "od_live_9f2c••••••••3ab1", used: "used_2h" },
  { id: "export", name: "k_export", key: "od_live_41de••••••••77c0", used: "used_yesterday" },
];

/** URLs and event names are what your own software matches on. */
export const WEBHOOKS: { id: string; url: string; events: string; last: Key; ok: boolean }[] = [
  {
    id: "call",
    url: "https://wagner-partner.at/hooks/call",
    events: "call.ended · transcript.ready",
    last: "w_delivered_0944",
    ok: true,
  },
  {
    id: "n8n",
    url: "https://n8n.local/webhook/telagent",
    events: "tool.failed · call.missed",
    last: "w_three_failures",
    ok: false,
  },
  {
    id: "booking",
    url: "https://wagner-partner.at/hooks/booking",
    events: "appointment.booked",
    last: "w_delivered_0812",
    ok: true,
  },
];

/** Tools Tel-Agent exposes over MCP. Write is off by default — see the warning. */
export const OUR_TOOLS: { name: string; desc: Key; scope: "read" | "act" | "write"; on: boolean }[] = [
  { name: "list_calls", desc: "tool_list_calls_desc", scope: "read", on: true },
  { name: "get_assistant", desc: "tool_get_assistant_desc", scope: "read", on: true },
  { name: "system_health", desc: "tool_system_health_desc", scope: "read", on: true },
  { name: "place_call", desc: "tool_place_call_desc", scope: "act", on: true },
  { name: "update_assistant", desc: "tool_update_assistant_desc", scope: "write", on: false },
  { name: "set_routing_rule", desc: "tool_set_routing_rule_desc", scope: "write", on: false },
];

/**
 * The audit vocabulary the screen knows how to name — `EVENTS` in
 * `api/models/audit.py`. An event outside this map is rendered verbatim as machine
 * output: a vocabulary entry added on the server must degrade to something readable,
 * never to a blank row.
 */
export const EVENT_LABEL: Record<string, Key> = {
  login_succeeded: "ev_login_succeeded",
  login_failed: "ev_login_failed",
  login_locked: "ev_login_locked",
  logout: "ev_logout",
  logout_all: "ev_logout_all",
  password_changed: "ev_password_changed",
  password_reset: "ev_password_reset",
  second_factor_used: "ev_second_factor_used",
  key_sign_in_succeeded: "ev_key_sign_in_succeeded",
  key_sign_in_failed: "ev_key_sign_in_failed",
  recovery_code_requested: "ev_recovery_code_requested",
  backup_downloaded: "ev_backup_downloaded",
  backup_deleted: "ev_backup_deleted",
  restore_staged: "ev_restore_staged",
  role_changed: "ev_role_changed",
  member_removed: "ev_member_removed",
  workspace_created: "ev_workspace_created",
  invite_created: "ev_invite_created",
  invite_accepted: "ev_invite_accepted",
};

export type Role = "owner" | "admin" | "reception" | "viewer" | "invited";

// The team list reads `/api/members` - the fixture members that sat here described
// phones, extensions and cross-workspace visibility that do not exist.

export const ROLE_LABEL: Record<Role, Key> = {
  owner: "role_owner",
  admin: "role_admin",
  reception: "role_reception",
  viewer: "role_viewer",
  invited: "role_invited",
};

export const ROLE_COLUMNS: Key[] = [
  "col_take_call",
  "col_read_transcripts",
  "col_manage",
  "col_billing",
];

export const ROLE_MATRIX: { role: Role; note: Key; cells: boolean[] }[] = [
  { role: "owner", note: "role_owner_note", cells: [true, true, true, true] },
  { role: "admin", note: "role_admin_note", cells: [true, true, true, false] },
  { role: "reception", note: "role_reception_note", cells: [true, true, false, false] },
  { role: "viewer", note: "role_viewer_note", cells: [false, true, false, false] },
];

