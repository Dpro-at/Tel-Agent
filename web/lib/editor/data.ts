/**
 * The assistant editor. The rail is grouped by what happens in a call, in order —
 * the phone rings, it works out what they want, it does something, they hang up.
 * That ordering is the point: a capability is easier to judge in the place it fires.
 *
 * Every piece of prose is a key into `locales/<lang>/editor.json`. What stays a
 * literal here is data: hostnames, calendar names, model identifiers, product
 * names, and the opening lines, which are what the assistant actually says.
 */

import type { EditorDictionary } from "@/app/[locale]/assistants/[id]/page";

export type Key = keyof EditorDictionary;

export type PanelId =
  | "contacts"
  | "persona"
  | "instructions"
  | "knowledge"
  | "booking"
  | "forward"
  | "apps"
  | "webhooks"
  | "email"
  | "sms";

export type RailRow = {
  icon: string;
  title: Key;
  panel: PanelId;
  /** A row's summary is either copy or a piece of data; never both. */
  value?: Key;
  valueText?: string;
  hint?: Key;
  enabled?: boolean;
};

/**
 * The rail, and why eight of its ten rows are drawn as not yet on.
 *
 * Only `persona` and `instructions` have a column behind them. The other eight read
 * a subsystem that has not been built - contacts matching, the knowledge library, the
 * calendar, call forwarding, installed apps, webhooks, email and sms - and each will
 * be wired by the milestone that builds it.
 *
 * They stay in the rail rather than disappearing from it: the four groups are how a
 * person learns what an assistant will eventually do, and a rail with two rows in it
 * teaches the opposite. The design already had the treatment for a row that is not on
 * yet, and it is the honest one - dashed, faded, with a line saying what it waits for.
 */
export const GROUPS: { id: string; label: Key; note: Key; rows: RailRow[] }[] = [
  {
    id: "ring",
    label: "group_ring",
    note: "group_ring_note",
    rows: [
      { icon: "contact", title: "row_contacts", panel: "contacts", enabled: false, hint: "pending_contacts" },
      { icon: "cube", title: "row_persona", panel: "persona" },
    ],
  },
  {
    id: "understand",
    label: "group_understand",
    note: "group_understand_note",
    rows: [
      { icon: "spark", title: "row_instructions", panel: "instructions" },
      { icon: "help", title: "row_knowledge", panel: "knowledge", enabled: false, hint: "pending_knowledge" },
    ],
  },
  {
    id: "act",
    label: "group_act",
    note: "group_act_note",
    rows: [
      { icon: "calendar", title: "row_booking", panel: "booking", enabled: false, hint: "pending_booking" },
      { icon: "forward", title: "row_forward", panel: "forward", enabled: false, hint: "pending_forward" },
      { icon: "plug", title: "row_apps", panel: "apps", enabled: false, hint: "pending_apps" },
      { icon: "webhook", title: "row_webhooks", panel: "webhooks", enabled: false, hint: "pending_webhooks" },
    ],
  },
  {
    id: "after",
    label: "group_after",
    note: "group_after_note",
    rows: [
      { icon: "mail", title: "row_email", panel: "email", enabled: false, hint: "pending_email" },
      { icon: "sms", title: "row_sms", panel: "sms", enabled: false, hint: "pending_sms" },
    ],
  },
];

/** A panel's heading is the same words as its rail row, so it reuses that key. */
export const PANEL_META: Record<PanelId, { title: Key; blurb: Key }> = {
  contacts: { title: "row_contacts", blurb: "blurb_contacts" },
  persona: { title: "row_persona", blurb: "blurb_persona" },
  instructions: { title: "row_instructions", blurb: "blurb_instructions" },
  knowledge: { title: "row_knowledge", blurb: "blurb_knowledge" },
  booking: { title: "row_booking", blurb: "blurb_booking" },
  forward: { title: "row_forward", blurb: "blurb_forward" },
  apps: { title: "row_apps", blurb: "blurb_apps" },
  webhooks: { title: "row_webhooks", blurb: "blurb_webhooks" },
  email: { title: "row_email", blurb: "blurb_email" },
  sms: { title: "row_sms", blurb: "blurb_sms" },
};

export const PROMPT_TEMPLATES: { id: string; label: Key }[] = [
  { id: "reception", label: "tpl_reception" },
  { id: "ooh", label: "tpl_ooh" },
  { id: "overflow", label: "tpl_overflow" },
  { id: "blank", label: "tpl_blank" },
];

/** A value is the product's own name plus, sometimes, a phrase about where it runs. */
export const TECHNICAL: {
  id: string;
  label: Key;
  help: Key;
  valueText?: string;
  valueKey?: Key;
  suffix?: Key;
}[] = [
  { id: "model", label: "t_model", help: "t_model_help", valueText: "claude-haiku-4-5 · Anthropic" },
  { id: "stt", label: "t_stt", help: "t_stt_help", valueText: "Whisper", suffix: "on_this_machine" },
  { id: "voice", label: "t_voice", help: "t_voice_help", valueText: "Piper", suffix: "on_this_machine" },
  { id: "answer", label: "t_answer", help: "t_answer_help", valueKey: "t_answer_value" },
  { id: "endpoint", label: "t_endpoint", help: "t_endpoint_help", valueKey: "t_endpoint_value" },
  { id: "maxlen", label: "t_maxlen", help: "t_maxlen_help", valueKey: "t_maxlen_value" },
];
