/**
 * The one place the browser talks to `api/`.
 *
 * Every failure from the server has the same envelope — `{ error: { code, message,
 * details, request_id } }` — so it is unwrapped once here rather than guessed at on
 * each of the twenty-nine screens.
 *
 * **Screens branch on `code`, never on `message`.** The message is prose written in
 * English on the server; the interface has its own translated string for each case in
 * `locales/`. Matching on it would break the first time somebody rewords an error, and
 * would show English to a German or Arabic user.
 */

import { activeWorkspaceId } from "./workspace";

/** Where `api/` is. Same-origin in production, a separate port in development. */
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ApiErrorBody = {
  code: string;
  message: string;
  details: Record<string, unknown>[] | null;
  request_id: string | null;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>[] | null;
  /** Shown to the user on an unexpected failure so they can quote it in a report. */
  readonly requestId: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code;
    this.details = body.details;
    this.requestId = body.request_id;
  }
}

/**
 * The server is unreachable — a different thing from the server refusing.
 *
 * The auth screens draw these two very differently: an offline banner naming the host,
 * against an inline message on the field. Conflating them tells somebody their password
 * is wrong when in fact their network is down.
 */
export class OfflineError extends Error {
  constructor() {
    super("The server could not be reached.");
    this.name = "OfflineError";
  }
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, ...rest } = init;

  // Which workspace this browser is acting in. Absent, the server assumes the
  // user's first workspace — the common case of belonging to one.
  const workspaceId = typeof window === "undefined" ? null : activeWorkspaceId();

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...rest,
      // The session is an HttpOnly cookie, so it has to be sent explicitly on a
      // cross-origin request. Without this every call is anonymous in development and
      // works in production, which is the worst way for a bug to behave.
      credentials: "include",
      headers: {
        ...(json === undefined ? {} : { "Content-Type": "application/json" }),
        ...(workspaceId === null ? {} : { "X-Workspace-Id": String(workspaceId) }),
        ...rest.headers,
      },
      body: json === undefined ? rest.body : JSON.stringify(json),
    });
  } catch {
    // `fetch` rejects only when the request never completed: DNS, refused connection,
    // CORS. An HTTP error status resolves normally and is handled below.
    throw new OfflineError();
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const error = (body as { error?: ApiErrorBody } | null)?.error;
    throw new ApiError(
      response.status,
      error ?? {
        code: "unknown",
        message: "Something went wrong.",
        details: null,
        request_id: response.headers.get("X-Request-Id"),
      },
    );
  }

  return body as T;
}

export type WorkspaceSummary = { id: number; name: string; role: string };

export type Me = {
  id: number;
  username: string;
  email: string | null;
  locale: string;
  theme: string;
  workspaces: WorkspaceSummary[];
};

// The session cookie is HttpOnly and lives on the API origin, so the dashboard's own
// middleware can never see it. This marker on the dashboard origin is what lets the
// middleware route a signed-out visitor to the sign-in screen without a round trip.
// It is a routing hint, not security: the API refuses regardless (D14's own words),
// and forging it buys a redirect to screens whose every request then gets a 401.
export const SIGNED_IN_HINT = "telagent_signed_in";

function setSignedInHint(on: boolean): void {
  if (typeof document === "undefined") return;
  document.cookie = on
    ? `${SIGNED_IN_HINT}=1; path=/; max-age=${14 * 24 * 60 * 60}; samesite=lax`
    : `${SIGNED_IN_HINT}=; path=/; max-age=0`;
}

export async function signIn(username: string, password: string): Promise<Me> {
  const me = await api<Me>("/api/auth/login", {
    method: "POST",
    json: { username, password },
  });
  setSignedInHint(true);
  return me;
}

export async function signOut(): Promise<void> {
  await api<void>("/api/auth/logout", { method: "POST" });
  setSignedInHint(false);
}

export function currentUser(): Promise<Me> {
  return api<Me>("/api/auth/me");
}

/** The interface language of the signed-in account — the committed tier only. */
export function updateMyLocale(locale: "en" | "de" | "ar"): Promise<Me> {
  return api<Me>("/api/auth/me", { method: "PATCH", json: { locale } });
}

/** When a `rate_limited` error says the lock lifts, or null if it did not say. */
export function lockedUntil(error: ApiError): Date | null {
  const raw = error.details?.[0]?.locked_until;
  if (typeof raw !== "string") return null;
  const when = new Date(raw);
  return Number.isNaN(when.getTime()) ? null : when;
}

// --- Account security: the settings profile tab -------------------------------

export type PasswordChanged = {
  changed: boolean;
  /** Every other session is ended when a password changes; the screen says so. */
  other_sessions_ended: number;
};

export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<PasswordChanged> {
  return api<PasswordChanged>("/api/auth/password", {
    method: "POST",
    json: { current_password: currentPassword, new_password: newPassword },
  });
}

export function signOutEverywhereElse(): Promise<{
  signed_out: boolean;
  other_sessions_ended: number;
}> {
  return api("/api/auth/logout-all", { method: "POST" });
}

export type AccountEvent = {
  /** From the closed vocabulary in `api/models/audit.py`. The screen translates the
   *  names it knows and shows an unknown one verbatim, as machine output — a new
   *  event must degrade to something readable, not to a blank row. */
  event: string;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
  details: Record<string, unknown> | null;
};

/** The signed-in account's own trail — most recent first, capped by the server. */
export function accountEvents(): Promise<AccountEvent[]> {
  return api<AccountEvent[]>("/api/auth/events");
}

// --- Recovery: the code, the key, and the new password -----------------------

export type ForgotResult = {
  /** "email" when a code may be on its way; "unavailable" when this installation
   *  cannot send mail at all - the screen's `no_mail` state. */
  delivery: "email" | "unavailable";
};

export function requestCode(username: string): Promise<ForgotResult> {
  return api<ForgotResult>("/api/auth/forgot", { method: "POST", json: { username } });
}

export async function verifyCode(
  username: string,
  code: string,
  purpose: "reset" | "second_factor" = "reset",
): Promise<{ ok: boolean }> {
  const result = await api<{ ok: boolean }>("/api/auth/code/verify", {
    method: "POST",
    json: { username, code, purpose },
  });
  // A verified second factor IS a sign-in; a reset verification is not.
  if (purpose === "second_factor") setSignedInHint(true);
  return result;
}

export type Challenge = {
  challenge: string;
  namespace: string;
  command: string;
  expires_in_seconds: number;
};

export function mintChallenge(username: string): Promise<Challenge> {
  return api<Challenge>("/api/auth/key/challenge", {
    method: "POST",
    json: { username },
  });
}

export async function verifyKeySignature(
  username: string,
  challenge: string,
  signature: string,
): Promise<{ ok: boolean }> {
  const result = await api<{ ok: boolean }>("/api/auth/key/verify", {
    method: "POST",
    json: { username, challenge, signature },
  });
  setSignedInHint(true);
  return result;
}

export function resetPassword(password: string): Promise<{ ok: boolean }> {
  return api("/api/auth/password/reset", { method: "POST", json: { password } });
}

/** From a `code_wrong` error: how many attempts remain on this code. */
export function attemptsLeft(error: ApiError): number | null {
  const raw = error.details?.[0]?.attempts_left;
  return typeof raw === "number" ? raw : null;
}

// --- Carrying the username across the reset steps ----------------------------
//
// `forgot`, `code` and `new-password` are separate pages, and the username typed on
// the first is needed by the second. It travels in sessionStorage rather than the
// URL: an account name in a URL lands in browser history, server logs and the
// Referer header, none of which need to know it.

const RESET_USERNAME_KEY = "telagent-reset-username";

export function rememberResetUsername(username: string): void {
  try {
    sessionStorage.setItem(RESET_USERNAME_KEY, username);
  } catch {
    // Storage can be unavailable (private windows, blocked site data). The code
    // screen asks the person to start over in that case, which costs one click.
  }
}

export function recallResetUsername(): string | null {
  try {
    return sessionStorage.getItem(RESET_USERNAME_KEY);
  } catch {
    return null;
  }
}

export function forgetResetUsername(): void {
  try {
    sessionStorage.removeItem(RESET_USERNAME_KEY);
  } catch {
    // Nothing to do: it expires with the tab anyway.
  }
}

// --- System health, and the log behind it ------------------------------------

export type ServiceState = "ok" | "degraded" | "down" | "not_configured";

export type ServiceRow = {
  id: string;
  state: ServiceState;
  /** Milliseconds, when there was something to measure. Null is not zero: a service
   *  with no timing must not be drawn as an instant one. */
  latency_ms: number | null;
  detail: string | null;
};

export type SystemStatus = {
  verdict: "ok" | "degraded" | "down";
  version: string;
  environment: string;
  services: ServiceRow[];
  storage: {
    total_bytes: number | null;
    free_bytes: number | null;
    parts: Record<string, number>;
  };
  scheduler: Record<
    string,
    { enabled: boolean; last_run_at: string | null; last_status: string | null; next_run_at: string | null }
  >;
};

export function systemStatus(): Promise<SystemStatus> {
  return api<SystemStatus>("/api/system/status");
}

export type LogLine = {
  time: string;
  level: string;
  service: string;
  message: string;
  request_id: string | null;
  exception: string | null;
};

export type LogPage = { entries: LogLine[]; capacity: number; retained: number };

export type LogFilter = "all" | "errors" | "warnings" | "calls";

export function systemLog(level: LogFilter = "all", limit = 100): Promise<LogPage> {
  return api<LogPage>(`/api/system/log?level=${level}&limit=${limit}`);
}

// --- Backup ------------------------------------------------------------------

export type BackupTarget = {
  path: string;
  configured: boolean;
  writable: boolean;
  detail: string;
  free_bytes: number | null;
};

export type Snapshot = {
  id: number;
  kind: "manual" | "nightly" | "before_update";
  status: "running" | "ok" | "unverified" | "failed";
  started_at: string;
  verified_at: string | null;
  size_bytes: number | null;
  recordings_included: boolean;
  schema_revision: string | null;
  error: string | null;
  present: boolean;
};

export type BackupOverview = {
  state: "ok" | "stale" | "none" | "running";
  target: BackupTarget;
  include_recordings: boolean;
  last_good_at: string | null;
  /** Computed by the server, which owns the clock that took the backup. */
  last_good_age_days: number | null;
  consecutive_failures: number;
  last_error: string | null;
  snapshots: Snapshot[];
  retention: { daily: number; weekly: number };
};

export function backupOverview(): Promise<BackupOverview> {
  return api<BackupOverview>("/api/backup");
}

export function checkBackupTarget(): Promise<BackupTarget> {
  return api<BackupTarget>("/api/backup/target/check");
}

export function runBackup(): Promise<{ queued: boolean; detail: string }> {
  return api("/api/backup/run", { method: "POST" });
}

export function deleteBackup(id: number): Promise<void> {
  return api<void>(`/api/backup/${id}`, { method: "DELETE" });
}

export type RestoreStaged = { staged: boolean; detail: string; warnings: string[] };

export function stageRestore(id: number, confirmDate: string): Promise<RestoreStaged> {
  return api<RestoreStaged>(`/api/backup/${id}/restore`, {
    method: "POST",
    json: { confirm_date: confirmDate },
  });
}

/** A download is a whole archive, so it is a navigation, not a fetch into memory. */
export function backupDownloadUrl(id: number): string {
  return `${API_URL}/api/backup/${id}/download`;
}

// --- Settings ----------------------------------------------------------------

export type SettingRow = {
  key: string;
  scope: string;
  kind: "string" | "integer" | "boolean";
  secret: boolean;
  description: string;
  /** A secret comes back masked, never in full — §B9.2. Writing the mask back is
   *  ignored by the server, which is what lets a form submit every field. */
  value: string | number | boolean | null;
};

export function allSettings(): Promise<SettingRow[]> {
  return api<SettingRow[]>("/api/settings");
}

/** To the signed-in admin's own address, never a typed one. */
export function sendTestMail(): Promise<{ sent: boolean; to: string }> {
  return api("/api/settings/mail/test", { method: "POST" });
}

export function saveSettings(
  values: Record<string, string | number | boolean | null>,
): Promise<{ written: string[]; ignored_masked: string[] }> {
  return api("/api/settings", { method: "PATCH", json: { values } });
}

// --- Members and workspaces ----------------------------------------------------

export type MemberRole = "owner" | "admin" | "reception" | "viewer" | "invited";

/** What an admin may set a member to. Ownership moves by transfer, not a picker,
 *  and an invitation is a pending fact, not a rank. */
export const ASSIGNABLE_ROLES = ["admin", "reception", "viewer"] as const;

export type Member = {
  user_id: number;
  username: string;
  email: string | null;
  role: MemberRole;
  joined_at: string;
};

export function membersList(): Promise<Member[]> {
  return api<Member[]>("/api/members");
}

export function changeMemberRole(
  userId: number,
  role: (typeof ASSIGNABLE_ROLES)[number],
): Promise<Member> {
  return api<Member>(`/api/members/${userId}`, { method: "PATCH", json: { role } });
}

/** Removes the membership, never the person. On an invited row this is
 *  "Cancel invite" — one action, one list. */
export function removeMember(userId: number): Promise<void> {
  return api<void>(`/api/members/${userId}`, { method: "DELETE" });
}

// Invitations - D-034. The admin half issues one-time links; the public half turns
// a link into an account with a self-chosen name.

export type InviteLink = {
  user_id: number;
  username: string;
  email: string;
  role: string;
  /** The path only; the dashboard prepends its own origin. */
  invite_path: string;
  expires_at: string;
  /** Whether a copy was also queued by email. */
  mailed: boolean;
};

export function createInvite(
  email: string,
  role: (typeof ASSIGNABLE_ROLES)[number],
): Promise<InviteLink> {
  return api<InviteLink>("/api/members/invites", { method: "POST", json: { email, role } });
}

/** "Resend invite": a fresh link and a fresh week; the old link dies. */
export function regenerateInvite(userId: number): Promise<InviteLink> {
  return api<InviteLink>(`/api/members/${userId}/invite-link`, { method: "POST" });
}

export type InvitePreview = {
  workspace: string;
  role: string;
  email: string | null;
  suggested_username: string;
  expires_at: string;
};

export function readInvite(token: string): Promise<InvitePreview> {
  return api<InvitePreview>(`/api/invites/${token}`);
}

export async function acceptInvite(
  token: string,
  username: string,
  password: string,
): Promise<Me> {
  const me = await api<Me>(`/api/invites/${token}/accept`, {
    method: "POST",
    json: { username, password },
  });
  // Accepting IS a sign-in: the response set the session cookie.
  setSignedInHint(true);
  return me;
}

export function createWorkspace(
  name: string,
  includeTeam: boolean,
): Promise<{ id: number; name: string; members: number }> {
  return api("/api/workspaces", {
    method: "POST",
    json: { name, include_team: includeTeam },
  });
}

// --- Apps: the extension registry ---------------------------------------------

export type InstalledApp = {
  slug: string;
  /** From the manifest. Our own applications are renamed into copy on the screen;
   *  anything else keeps the name its author declared. */
  name: string;
  version: string | null;
  origin: string;
  category: string;
  description: string;
  /** What the extension asked to be allowed to do — the reviewable claim. */
  scopes: string[];
  hooks: string[];
  /** Live in this process right now. In the table and not running is a real state. */
  running: boolean;
};

export type RefusedApp = { slug: string; reason: string };

export type AppsOverview = {
  installed: InstalledApp[];
  refused: RefusedApp[];
};

export function appsOverview(): Promise<AppsOverview> {
  return api<AppsOverview>("/api/apps");
}

// --- Conversations: the transcript archive, read ------------------------------

export type ThreadStatus = "open" | "closed";
export type ThreadHandling = "ai" | "human" | "blocked";

export type Thread = {
  id: number;
  /** The channel's kind — `web`, `phone`, `whatsapp`, … `ChannelMark` knows them. */
  channel: string;
  direction: "inbound" | "outbound";
  status: ThreadStatus;
  handling: ThreadHandling | null;
  intent: string | null;
  started_at: string;
  ended_at: string | null;
  /** Whoever is on the other end, as the channel knows them — a caller's number, a
   *  chat id. Null for an anonymous web visitor; the screen has a word for that. */
  who: string | null;
  /** And as the phonebook knows them, when it does. Null for a caller nobody has
   *  named yet — the number stays the honest headline. */
  who_name: string | null;
  preview: string | null;
  message_count: number;
  is_call: boolean;
};

export type ThreadPage = {
  threads: Thread[];
  /** Said by the server rather than inferred from a short page. */
  has_more: boolean;
};

export type ThreadMessage = {
  id: number;
  /** Milliseconds from the start of the conversation, not a clock. */
  ts_ms: number;
  speaker: "caller" | "agent" | "human";
  text: string;
  /** An operator's instruction to the agent mid-conversation — part of the record,
   *  flagged because the customer never saw it. */
  is_whisper: boolean;
  stt_confidence: number | null;
  language: string | null;
};

export type ThreadCall = {
  from_e164: string | null;
  billable_seconds: number | null;
  provider_cost_micros: number | null;
  /** Whether audio exists — never where it is. */
  has_recording: boolean;
};

export type ThreadDetail = Thread & {
  summary: string | null;
  messages: ThreadMessage[];
  call: ThreadCall | null;
};

/** One list page must agree with the server's cap in `api/conversations.py`. */
export const THREADS_PAGE = 50;

export function conversationList(filters: {
  channel?: string;
  status?: ThreadStatus;
  q?: string;
  offset?: number;
  /** Fewer than a page, for a screen that shows a handful and links to the rest. */
  limit?: number;
}): Promise<ThreadPage> {
  const query = new URLSearchParams();
  if (filters.limit) query.set("limit", String(filters.limit));
  if (filters.channel) query.set("channel", filters.channel);
  if (filters.status) query.set("status", filters.status);
  if (filters.q) query.set("q", filters.q);
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.size > 0 ? `?${query}` : "";
  return api<ThreadPage>(`/api/conversations${suffix}`);
}

/**
 * The two numbers the home screen opens with.
 *
 * `since` is sent by the browser because the server has no timezone for a workspace:
 * midnight in Vienna is not midnight in UTC, and the reader means their own day.
 *
 * `by_agent` is null when nothing has recorded who took a conversation - which is not
 * the same as the agent having taken none, and must not be drawn as a zero.
 */
export type HomeSummary = {
  since: string;
  conversations: number;
  by_agent: number | null;
  waiting: number;
};

export function homeSummary(since: Date): Promise<HomeSummary> {
  return api<HomeSummary>(`/api/home?since=${encodeURIComponent(since.toISOString())}`);
}

export function conversationDetail(id: number): Promise<ThreadDetail> {
  return api<ThreadDetail>(`/api/conversations/${id}`);
}

export type ConversationChannel = {
  id: number;
  kind: string;
  name: string | null;
  thread_count: number;
};

/** What the filter chips should offer — the channels this workspace has used. */
export function conversationChannels(): Promise<ConversationChannel[]> {
  return api<ConversationChannel[]>("/api/conversations/meta/channels");
}

// --- Numbers: the registry ----------------------------------------------------

export type PhoneNumber = {
  id: number;
  e164: string;
  provider: string;
  /** §B5 decision 3 — who holds the number. Everything added from this dashboard is
   *  `customer`; a `platform` number belongs to Tel-Agent Cloud and cannot be
   *  released here. */
  owner: "customer" | "platform";
  status: "active" | "disabled";
  created_at: string;
};

export function numbersList(): Promise<PhoneNumber[]> {
  return api<PhoneNumber[]>("/api/numbers");
}

export function addNumber(e164: string, provider: string): Promise<PhoneNumber> {
  return api<PhoneNumber>("/api/numbers", { method: "POST", json: { e164, provider } });
}

export function setNumberStatus(
  id: number,
  status: "active" | "disabled",
): Promise<PhoneNumber> {
  return api<PhoneNumber>(`/api/numbers/${id}`, { method: "PATCH", json: { status } });
}

/** Removes the record here — the provider contract is the customer's own affair. */
export function releaseNumber(id: number): Promise<void> {
  return api<void>(`/api/numbers/${id}`, { method: "DELETE" });
}

// --- Contacts: the phonebook ---------------------------------------------------

export type Contact = {
  id: number;
  e164: string;
  name: string;
  tags: string[];
  notes: string | null;
  created_at: string;
  /** When this number last reached the business, out of the archive. */
  last_heard_at: string | null;
};

export type ContactPage = { contacts: Contact[]; has_more: boolean };

/** One list page must agree with the server's cap in `api/routes/contacts.py`. */
export const CONTACTS_PAGE = 50;

export function contactsList(filters: { q?: string; offset?: number }): Promise<ContactPage> {
  const query = new URLSearchParams();
  if (filters.q) query.set("q", filters.q);
  if (filters.offset) query.set("offset", String(filters.offset));
  const suffix = query.size > 0 ? `?${query}` : "";
  return api<ContactPage>(`/api/contacts${suffix}`);
}

export function addContact(contact: {
  e164: string;
  name: string;
  tags?: string[];
  notes?: string;
}): Promise<Contact> {
  return api<Contact>("/api/contacts", { method: "POST", json: contact });
}

/** The name, the tags, the note — never the number. A different number is a
 *  different contact. */
export function changeContact(
  id: number,
  contact: { name: string; tags?: string[]; notes?: string | null },
): Promise<Contact> {
  return api<Contact>(`/api/contacts/${id}`, { method: "PATCH", json: contact });
}

/** Removes the name, not the history — conversations keep their rows. */
export function removeContact(id: number): Promise<void> {
  return api<void>(`/api/contacts/${id}`, { method: "DELETE" });
}

// --- Assistants: who answers, and in what words --------------------------------

/** Mirrors ASSISTANT_TEMPLATES in api/models/assistant.py. */
export type AssistantTemplate = "reception" | "ooh" | "overflow" | "blank";
export type AssistantStatus = "active" | "paused";

export type AssistantTool = {
  name: string;
  available: boolean;
  /** The subsystem it waits for, or null when it is ready. */
  waiting_on: string | null;
};

/** Served rather than copied, so the screen cannot drift from §B7. */
export function assistantTools(): Promise<AssistantTool[]> {
  return api<AssistantTool[]>("/api/assistants/tools");
}

export type Assistant = {
  id: number;
  name: string;
  /** What it is for, in the customer's words - "Reception, weekdays". */
  role: string | null;
  template: AssistantTemplate;
  status: AssistantStatus;
  /** Who it is. */
  persona: string;
  /** What it may and may not do. */
  instructions: string;
  language: string | null;
  model: string | null;
  /** Which of §B7's tools it may reach. Empty is a real answer. */
  tools: string[];
  created_at: string;
  updated_at: string;
};

export function assistantsList(): Promise<Assistant[]> {
  return api<Assistant[]>("/api/assistants");
}

export function assistant(id: number): Promise<Assistant> {
  return api<Assistant>(`/api/assistants/${id}`);
}

export function addAssistant(fields: {
  name: string;
  role?: string | null;
  template?: AssistantTemplate;
  persona?: string;
  instructions?: string;
}): Promise<Assistant> {
  return api<Assistant>("/api/assistants", { method: "POST", json: fields });
}

/**
 * Any subset, because the editor saves one panel at a time.
 *
 * An absent key is left alone and an explicit `null` clears the field - the server
 * reads the two apart, so building this object by spreading a whole form would send
 * panels the person never opened.
 */
export function changeAssistant(
  id: number,
  fields: Partial<{
    name: string;
    role: string | null;
    template: AssistantTemplate;
    status: AssistantStatus;
    persona: string;
    instructions: string;
    language: string | null;
    model: string | null;
    tools: string[];
  }>,
): Promise<Assistant> {
  return api<Assistant>(`/api/assistants/${id}`, { method: "PATCH", json: fields });
}

/** Real deletion. The conversations it answered keep their transcripts. */
export function removeAssistant(id: number): Promise<void> {
  return api<void>(`/api/assistants/${id}`, { method: "DELETE" });
}

// --- Knowledge: what an assistant may read -------------------------------------

export type KnowledgeSource = {
  id: number;
  title: string;
  content: string;
  /** Null means every assistant in this workspace, which is most knowledge. */
  assistant_id: number | null;
  assistant_name: string | null;
  created_at: string;
  updated_at: string;
};

/** Mirrors CONTENT_MAX in api/models/knowledge.py, so the counter means refusal. */
export const KNOWLEDGE_CONTENT_MAX = 20_000;

export function knowledgeList(): Promise<KnowledgeSource[]> {
  return api<KnowledgeSource[]>("/api/knowledge");
}

export function addKnowledge(source: {
  title: string;
  content: string;
  assistant_id?: number | null;
}): Promise<KnowledgeSource> {
  return api<KnowledgeSource>("/api/knowledge", { method: "POST", json: source });
}

/** Any subset. An absent key is left alone; `assistant_id: null` is "every one". */
export function changeKnowledge(
  id: number,
  source: Partial<{ title: string; content: string; assistant_id: number | null }>,
): Promise<KnowledgeSource> {
  return api<KnowledgeSource>(`/api/knowledge/${id}`, { method: "PATCH", json: source });
}

export function removeKnowledge(id: number): Promise<void> {
  return api<void>(`/api/knowledge/${id}`, { method: "DELETE" });
}

// --- Webhooks: how your own software hears what happened -----------------------

export type Webhook = {
  id: number;
  name: string | null;
  url: string;
  events: string[];
  enabled: boolean;
  /** Last four characters. The secret itself is returned once and never again. */
  secret_preview: string;
  created_at: string;
  updated_at: string;
};

/** Only ever the answer to creating one or rotating one. */
export type WebhookWithSecret = Webhook & { secret: string };

/** Served rather than copied, so the list cannot drift from the server's. */
export function webhookEvents(): Promise<string[]> {
  return api<string[]>("/api/webhooks/events");
}

export function webhooksList(): Promise<Webhook[]> {
  return api<Webhook[]>("/api/webhooks");
}

/** The response carries the signing secret. It is the only time it can be read. */
export function addWebhook(hook: {
  url: string;
  events: string[];
  name?: string | null;
}): Promise<WebhookWithSecret> {
  return api<WebhookWithSecret>("/api/webhooks", { method: "POST", json: hook });
}

export function changeWebhook(
  id: number,
  hook: Partial<{ url: string; events: string[]; name: string | null; enabled: boolean }>,
): Promise<Webhook> {
  return api<Webhook>(`/api/webhooks/${id}`, { method: "PATCH", json: hook });
}

/** Replaces the secret and returns the new one once, on the same terms. */
export function rotateWebhookSecret(id: number): Promise<WebhookWithSecret> {
  return api<WebhookWithSecret>(`/api/webhooks/${id}/secret`, { method: "POST" });
}

export function removeWebhook(id: number): Promise<void> {
  return api<void>(`/api/webhooks/${id}`, { method: "DELETE" });
}

// --- The web chat channel ------------------------------------------------------

export type WebChannel = {
  enabled: boolean;
  /** Empty refuses everything. It is not "unrestricted" - see §B14. */
  allowed_origins: string[];
  recaptcha_site_key: string | null;
  recaptcha_threshold: number;
  /** Masked, or null. The secret itself never leaves the server after it is saved. */
  recaptcha_secret_preview: string | null;
  embed_path: string;
  embed_snippet: string;
};

export function webChannel(): Promise<WebChannel> {
  return api<WebChannel>("/api/channels/web");
}

/**
 * Any subset. `recaptcha_secret` is write-only: omit it to leave the stored one
 * alone, send "" to remove it, and never send back the mask that was displayed -
 * the server ignores an echoed mask, but not sending it is the honest half.
 */
export function saveWebChannel(
  fields: Partial<{
    enabled: boolean;
    allowed_origins: string[];
    recaptcha_site_key: string | null;
    recaptcha_secret: string;
    recaptcha_threshold: number;
  }>,
): Promise<WebChannel> {
  return api<WebChannel>("/api/channels/web", { method: "PUT", json: fields });
}

// --- Routing rules ------------------------------------------------------------

export type RuleAction = "pass" | "block" | "ai";

export type RoutingRule = {
  id: number;
  /** An exact E.164, or a prefix ending in `*` — the only two shapes a rule takes. */
  e164_or_pattern: string;
  action: RuleAction;
  note: string | null;
  created_at: string;
  /** The latest phone call from a matching number, out of the archive. Null when
   *  none is stored — which for a blocked number is the good outcome. */
  last_called_at: string | null;
  last_handling: string | null;
};

export function rulesList(): Promise<RoutingRule[]> {
  return api<RoutingRule[]>("/api/rules");
}

export function addRule(
  pattern: string,
  action: RuleAction,
  note?: string,
): Promise<RoutingRule> {
  return api<RoutingRule>("/api/rules", {
    method: "POST",
    json: { e164_or_pattern: pattern, action, note: note || null },
  });
}

/** Moving a rule to another column is changing its action. */
export function changeRule(
  id: number,
  action: RuleAction,
  note?: string | null,
): Promise<RoutingRule> {
  return api<RoutingRule>(`/api/rules/${id}`, {
    method: "PATCH",
    json: { action, note: note ?? null },
  });
}

export function removeRule(id: number): Promise<void> {
  return api<void>(`/api/rules/${id}`, { method: "DELETE" });
}

// --- Notifications -----------------------------------------------------------

export type NotificationCategory = "failure" | "review" | "missed" | "system";

export type NotificationItem = {
  id: number;
  category: NotificationCategory;
  needs_decision: boolean;
  /** Which message, from the catalogue in `api/notifications.py`. The sentence itself
   *  lives in each locale's `notifications.json` as `msg_<key>`, so the screen renders
   *  it in the reader's language rather than the server's. */
  message_key: string;
  /** What goes in the placeholders — data only: a path, a count, a name, a date. */
  params: Record<string, string | number>;
  /** The machine's own words, if there were any. Never translated, shown as machine
   *  output rather than folded into the sentence around it. */
  detail: string | null;
  primary_action: string;
  action_payload: Record<string, unknown> | null;
  conversation_id: number | null;
  created_at: string;
  resolved_at: string | null;
};

export type NotificationList = {
  /** Waiting on a decision only a person can make. */
  waiting: NotificationItem[];
  /** What happened while nobody was watching. */
  log: NotificationItem[];
  open_count: number;
};

export function notificationList(category?: NotificationCategory): Promise<NotificationList> {
  const query = category ? `?category=${category}` : "";
  return api<NotificationList>(`/api/notifications${query}`);
}

export function resolveNotification(id: number): Promise<NotificationItem> {
  return api<NotificationItem>(`/api/notifications/${id}/resolve`, { method: "POST" });
}

export function markLogRead(): Promise<{ resolved: number; still_waiting: number }> {
  return api("/api/notifications/mark-log-read", { method: "POST" });
}
