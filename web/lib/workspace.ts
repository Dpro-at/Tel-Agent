/**
 * Which workspace this browser is acting in.
 *
 * The server assumes the first workspace when the header is absent — the common case
 * of belonging to one — so this store only exists for people who belong to several.
 * It is a per-browser convenience in `localStorage`: losing it costs one click in
 * the switcher, and the server re-checks membership on every request regardless, so
 * a stale or forged value buys a 403, not access.
 */

const KEY = "telagent-workspace";

export function activeWorkspaceId(): number | null {
  try {
    const raw = localStorage.getItem(KEY);
    const id = raw === null ? NaN : Number(raw);
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

export function setActiveWorkspaceId(id: number | null): void {
  try {
    if (id === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, String(id));
  } catch {
    // Storage disabled: the switcher still works for this page load, it just does
    // not remember — the same trade the theme toggle already makes.
  }
}
