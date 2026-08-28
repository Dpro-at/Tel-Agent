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

/** When a `rate_limited` error says the lock lifts, or null if it did not say. */
export function lockedUntil(error: ApiError): Date | null {
  const raw = error.details?.[0]?.locked_until;
  if (typeof raw !== "string") return null;
  const when = new Date(raw);
  return Number.isNaN(when.getTime()) ? null : when;
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

export function saveSettings(
  values: Record<string, string | number | boolean | null>,
): Promise<{ written: string[]; ignored_masked: string[] }> {
  return api("/api/settings", { method: "PATCH", json: { values } });
}

// --- Notifications -----------------------------------------------------------

export type NotificationCategory = "failure" | "review" | "missed" | "system";

export type NotificationItem = {
  id: number;
  category: NotificationCategory;
  needs_decision: boolean;
  /** Prose written by whatever raised this, in the server's language — not a locale
   *  key. The screen renders it as given rather than pretending it is translated. */
  title: string;
  body: string | null;
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
