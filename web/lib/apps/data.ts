/**
 * The app catalogue. `mcp` entries have no app of their own and do not need one —
 * the customer points Tel-Agent at an MCP server and its tools become callable.
 *
 * Every piece of prose is a key into `locales/<lang>/apps.json`. A third-party
 * product keeps its own name as a literal; an app that is ours is named in copy.
 */

import type { AppsDictionary } from "@/app/[locale]/apps/page";

export type Key = keyof AppsDictionary;

export type Install = "installed" | "install" | "planned" | "mcp";
export type Origin = "official" | "community" | "planned" | "mcp";

export type App = {
  id: string;
  category: string;
  mark: string;
  /** Our own apps are named in copy; a third-party product keeps its own name. */
  name?: Key;
  nameText?: string;
  origin: Origin;
  /** Machine-readable: version and download size. Absent for anything unbuilt. */
  version?: string;
  /** When it is expected instead, which is copy — quarters and years are worded. */
  eta?: Key;
  desc: Key;
  install: Install;
  warn?: Key;
};

export const CATEGORIES: { id: string; label: Key; note: Key }[] = [
  { id: "system", label: "cat_system", note: "cat_system_note" },
  { id: "channels", label: "cat_channels", note: "cat_channels_note" },
  { id: "calendars", label: "cat_calendars", note: "cat_calendars_note" },
  { id: "tools", label: "cat_tools", note: "cat_tools_note" },
  { id: "notifications", label: "cat_notifications", note: "cat_notifications_note" },
  { id: "pbx", label: "cat_pbx", note: "cat_pbx_note" },
  { id: "sip", label: "cat_sip", note: "cat_sip_note" },
  { id: "health", label: "cat_health", note: "cat_health_note" },
  { id: "property", label: "cat_property", note: "cat_property_note" },
  { id: "hospitality", label: "cat_hospitality", note: "cat_hospitality_note" },
  { id: "accounting", label: "cat_accounting", note: "cat_accounting_note" },
  { id: "beauty", label: "cat_beauty", note: "cat_beauty_note" },
  { id: "analytics", label: "cat_analytics", note: "cat_analytics_note" },
];

export const CATALOGUE: App[] = [
  { id: "agent_core", category: "system", mark: "◉", name: "app_agent_core_name", origin: "official", version: "v4.1.0 · 84 MB", desc: "app_agent_core_desc", install: "installed" },
  { id: "german_voices", category: "system", mark: "♪", name: "app_german_voices_name", origin: "official", version: "v2.2.0 · 310 MB", desc: "app_german_voices_desc", install: "installed" },
  { id: "database", category: "system", mark: "▤", name: "app_database_name", origin: "official", version: "v1.5.0 · 22 MB", desc: "app_database_desc", install: "installed" },
  { id: "tls_certificates", category: "system", mark: "⛨", name: "app_tls_certificates_name", origin: "official", version: "v1.0.4 · 6 MB", desc: "app_tls_certificates_desc", install: "install" },
  { id: "signal", category: "channels", mark: "S", nameText: "Signal", origin: "community", version: "v1.2.0 · 4.1 MB", desc: "app_signal_desc", install: "install" },
  { id: "sms", category: "channels", mark: "M", name: "app_sms_name", origin: "official", version: "v2.0.1 · 1.8 MB", desc: "app_sms_desc", install: "install" },
  { id: "web_chat", category: "channels", mark: "◍", name: "app_web_chat_name", origin: "official", version: "v1.6.3 · 220 KB", desc: "app_web_chat_desc", install: "installed" },
  { id: "email", category: "channels", mark: "✉", name: "app_email_name", origin: "official", version: "v1.1.0 · 900 KB", desc: "app_email_desc", install: "install" },
  { id: "instagram_dm", category: "channels", mark: "I", name: "app_instagram_dm_name", origin: "community", version: "v0.4.2 · 3.2 MB", desc: "app_instagram_dm_desc", install: "install", warn: "app_instagram_dm_warn" },
  { id: "matrix", category: "channels", mark: "#", nameText: "Matrix", origin: "community", version: "v1.0.0 · 2.6 MB", desc: "app_matrix_desc", install: "install" },
  { id: "caldav", category: "calendars", mark: "◷", nameText: "CalDAV", origin: "official", version: "v2.3.0 · 1.1 MB", desc: "app_caldav_desc", install: "installed" },
  { id: "google_calendar", category: "calendars", mark: "G", nameText: "Google Calendar", origin: "official", version: "v1.9.4 · 1.4 MB", desc: "app_google_calendar_desc", install: "install" },
  { id: "local_transcription", category: "tools", mark: "◰", name: "app_local_transcription_name", origin: "official", version: "v3.0.2 · 620 MB", desc: "app_local_transcription_desc", install: "installed" },
  { id: "spam_list", category: "tools", mark: "⛉", name: "app_spam_list_name", origin: "community", version: "v4.7.1 · 12 MB", desc: "app_spam_list_desc", install: "installed" },
  { id: "backup", category: "tools", mark: "↺", name: "app_backup_name", origin: "official", version: "v1.3.0 · 800 KB", desc: "app_backup_desc", install: "install" },
  { id: "slack", category: "notifications", mark: "◆", nameText: "Slack", origin: "community", version: "v1.0.6 · 1.9 MB", desc: "app_slack_desc", install: "install" },
  { id: "ntfy", category: "notifications", mark: "◈", nameText: "ntfy", origin: "community", version: "v1.2.2 · 300 KB", desc: "app_ntfy_desc", install: "install" },
  { id: "asterisk_freepbx", category: "pbx", mark: "✳", nameText: "Asterisk / FreePBX", origin: "official", version: "v2.1.0 · 3.4 MB", desc: "app_asterisk_freepbx_desc", install: "install" },
  { id: "3cx", category: "pbx", mark: "3", nameText: "3CX", origin: "official", version: "v1.4.0 · 2.2 MB", desc: "app_3cx_desc", install: "install" },
  { id: "yeastar", category: "pbx", mark: "Y", nameText: "Yeastar", origin: "community", version: "v0.8.0 · 1.9 MB", desc: "app_yeastar_desc", install: "install", warn: "app_yeastar_warn" },
  { id: "pascom", category: "pbx", mark: "p", nameText: "pascom", origin: "community", version: "v0.6.1 · 1.6 MB", desc: "app_pascom_desc", install: "install" },
  { id: "nfon", category: "pbx", mark: "N", nameText: "NFON", origin: "planned", eta: "app_nfon_eta", desc: "app_nfon_desc", install: "planned" },
  { id: "starface", category: "pbx", mark: "S", nameText: "STARFACE", origin: "planned", eta: "app_starface_eta", desc: "app_starface_desc", install: "planned" },
  { id: "swyx_enreach", category: "pbx", mark: "sw", nameText: "Swyx (Enreach)", origin: "planned", eta: "app_swyx_enreach_eta", desc: "app_swyx_enreach_desc", install: "planned" },
  { id: "innovaphone", category: "pbx", mark: "in", nameText: "innovaphone", origin: "planned", eta: "app_innovaphone_eta", desc: "app_innovaphone_desc", install: "planned" },
  { id: "wildix", category: "pbx", mark: "W", nameText: "Wildix", origin: "planned", eta: "app_wildix_eta", desc: "app_wildix_desc", install: "planned" },
  { id: "teams_phone", category: "pbx", mark: "T", nameText: "Teams Phone", origin: "planned", eta: "app_teams_phone_eta", desc: "app_teams_phone_desc", install: "planned" },
  { id: "generic_sip_trunk", category: "sip", mark: "SIP", name: "app_generic_sip_trunk_name", origin: "official", version: "v3.0.0 · 640 KB", desc: "app_generic_sip_trunk_desc", install: "installed" },
  { id: "easybell_presets", category: "sip", mark: "eb", name: "app_easybell_presets_name", origin: "official", version: "v1.2.0 · 210 KB", desc: "app_easybell_presets_desc", install: "installed" },
  { id: "sipgate", category: "sip", mark: "sg", nameText: "sipgate", origin: "official", version: "v1.1.0 · 190 KB", desc: "app_sipgate_desc", install: "install" },
  { id: "telekom_companyflex", category: "sip", mark: "DT", nameText: "Telekom CompanyFlex", origin: "community", version: "v0.5.0 · 240 KB", desc: "app_telekom_companyflex_desc", install: "install", warn: "app_telekom_companyflex_warn" },
  { id: "toplink", category: "sip", mark: "tl", nameText: "toplink", origin: "planned", eta: "app_toplink_eta", desc: "app_toplink_desc", install: "planned" },
  { id: "ecotel", category: "sip", mark: "ec", nameText: "ecotel", origin: "planned", eta: "app_ecotel_eta", desc: "app_ecotel_desc", install: "planned" },
  { id: "twilio", category: "sip", mark: "Tw", nameText: "Twilio", origin: "official", version: "v1.3.0 · 380 KB", desc: "app_twilio_desc", install: "install" },
  { id: "a1_telekom_austria", category: "sip", mark: "A1", nameText: "A1 Telekom Austria", origin: "community", version: "v0.7.0 · 180 KB", desc: "app_a1_telekom_austria_desc", install: "install" },
  { id: "tomedo", category: "health", mark: "Td", nameText: "Tomedo", origin: "mcp", desc: "app_tomedo_desc", install: "mcp" },
  { id: "ivoris", category: "health", mark: "Iv", nameText: "Ivoris", origin: "mcp", desc: "app_ivoris_desc", install: "mcp" },
  { id: "dr_flex", category: "health", mark: "Dr", nameText: "Dr. Flex", origin: "community", version: "v0.4.0 · 900 KB", desc: "app_dr_flex_desc", install: "install", warn: "app_dr_flex_warn" },
  { id: "samedi", category: "health", mark: "Sa", nameText: "Samedi", origin: "planned", eta: "app_samedi_eta", desc: "app_samedi_desc", install: "planned" },
  { id: "doctolib", category: "health", mark: "Dc", nameText: "Doctolib", origin: "planned", eta: "app_doctolib_eta", desc: "app_doctolib_desc", install: "planned" },
  { id: "latido", category: "health", mark: "La", nameText: "Latido", origin: "planned", eta: "app_latido_eta", desc: "app_latido_desc", install: "planned" },
  { id: "pharmacy_stock_win_pharma_lgpi", category: "health", mark: "Rx", name: "app_pharmacy_stock_win_pharma_lgpi_name", origin: "mcp", desc: "app_pharmacy_stock_win_pharma_lgpi_desc", install: "mcp" },
  { id: "onoffice", category: "property", mark: "oO", nameText: "onOffice", origin: "community", version: "v0.9.0 · 1.2 MB", desc: "app_onoffice_desc", install: "install" },
  { id: "casavi", category: "property", mark: "cs", nameText: "casavi", origin: "community", version: "v0.6.0 · 1.0 MB", desc: "app_casavi_desc", install: "install" },
  { id: "immoware24", category: "property", mark: "Iw", nameText: "Immoware24", origin: "planned", eta: "app_immoware24_eta", desc: "app_immoware24_desc", install: "planned" },
  { id: "domus", category: "property", mark: "DM", nameText: "DOMUS", origin: "planned", eta: "app_domus_eta", desc: "app_domus_desc", install: "planned" },
  { id: "etg24", category: "property", mark: "et", nameText: "etg24", origin: "mcp", desc: "app_etg24_desc", install: "mcp" },
  { id: "propstack", category: "property", mark: "Pr", nameText: "Propstack", origin: "mcp", desc: "app_propstack_desc", install: "mcp" },
  { id: "idwell", category: "property", mark: "ID", nameText: "IDwell", origin: "planned", eta: "app_idwell_eta", desc: "app_idwell_desc", install: "planned" },
  { id: "mews", category: "hospitality", mark: "MW", nameText: "MEWS", origin: "community", version: "v0.8.0 · 1.4 MB", desc: "app_mews_desc", install: "install" },
  { id: "opentable", category: "hospitality", mark: "OT", nameText: "OpenTable", origin: "mcp", desc: "app_opentable_desc", install: "mcp" },
  { id: "resmio", category: "hospitality", mark: "rm", nameText: "resmio", origin: "community", version: "v0.5.0 · 700 KB", desc: "app_resmio_desc", install: "install" },
  { id: "protel", category: "hospitality", mark: "Pt", nameText: "protel", origin: "planned", eta: "app_protel_eta", desc: "app_protel_desc", install: "planned" },
  { id: "gastronovi", category: "hospitality", mark: "gn", nameText: "gastronovi", origin: "planned", eta: "app_gastronovi_eta", desc: "app_gastronovi_desc", install: "planned" },
  { id: "smoobu", category: "hospitality", mark: "Sm", nameText: "Smoobu", origin: "mcp", desc: "app_smoobu_desc", install: "mcp" },
  { id: "toast", category: "hospitality", mark: "Ts", nameText: "Toast", origin: "planned", eta: "app_toast_eta", desc: "app_toast_desc", install: "planned" },
  { id: "datev", category: "accounting", mark: "DV", nameText: "DATEV", origin: "planned", eta: "app_datev_eta", desc: "app_datev_desc", install: "planned" },
  { id: "bexio", category: "accounting", mark: "bx", nameText: "bexio", origin: "mcp", desc: "app_bexio_desc", install: "mcp" },
  { id: "odoo", category: "accounting", mark: "Od", nameText: "Odoo", origin: "community", version: "v0.7.0 · 1.1 MB", desc: "app_odoo_desc", install: "install" },
  { id: "stripe", category: "accounting", mark: "St", nameText: "Stripe", origin: "mcp", desc: "app_stripe_desc", install: "mcp", warn: "app_stripe_warn" },
  { id: "selectline", category: "accounting", mark: "SL", nameText: "SelectLine", origin: "planned", eta: "app_selectline_eta", desc: "app_selectline_desc", install: "planned" },
  { id: "planity", category: "beauty", mark: "Pl", nameText: "Planity", origin: "planned", eta: "app_planity_eta", desc: "app_planity_desc", install: "planned" },
  { id: "phorest", category: "beauty", mark: "Ph", nameText: "Phorest", origin: "planned", eta: "app_phorest_eta", desc: "app_phorest_desc", install: "planned" },
  { id: "booksy", category: "beauty", mark: "Bo", nameText: "Booksy", origin: "mcp", desc: "app_booksy_desc", install: "mcp" },
  { id: "magicline", category: "beauty", mark: "Mg", nameText: "Magicline", origin: "planned", eta: "app_magicline_eta", desc: "app_magicline_desc", install: "planned" },
  { id: "langfuse", category: "analytics", mark: "Lf", nameText: "Langfuse", origin: "official", version: "v1.0.2 · 1.8 MB", desc: "app_langfuse_desc", install: "install" },
  { id: "fireflies_ai", category: "analytics", mark: "Ff", nameText: "Fireflies.ai", origin: "mcp", desc: "app_fireflies_ai_desc", install: "mcp", warn: "app_fireflies_ai_warn" },
  { id: "gong", category: "analytics", mark: "Gg", nameText: "Gong", origin: "planned", eta: "app_gong_eta", desc: "app_gong_desc", install: "planned" },
  { id: "grafana", category: "analytics", mark: "Gr", nameText: "Grafana", origin: "official", version: "v1.4.0 · 2.1 MB", desc: "app_grafana_desc", install: "install" },
];

// The installed tab reads `/api/apps` — the manifests the registry actually loaded.
// The fixture lists that used to sit here claimed WhatsApp and Telegram were
// installed, which was a drawing of extensions that do not exist; they went when the
// tab was wired. The store catalogue above stays: it is a roadmap and says so.

/**
 * A stable colour per app. Keyed on the id, never the displayed name — a name
 * that changes with the language would change the colour with it.
 */
export function tintFor(id: string): { border: string; background: string; color: string } {
  let hue = 0;
  for (let index = 0; index < id.length; index += 1) {
    hue = (hue * 31 + id.charCodeAt(index)) % 360;
  }
  return {
    border: `oklch(0.58 0.16 ${hue} / 0.36)`,
    background: `oklch(0.58 0.16 ${hue} / 0.16)`,
    color: `oklch(0.55 0.17 ${hue})`,
  };
}
