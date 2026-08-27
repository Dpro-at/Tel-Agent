import { NextResponse, type NextRequest } from "next/server";

import { DEFAULT_LOCALE, LOCALES } from "@/lib/locales";

/**
 * The dashboard-origin marker set by `lib/api.ts` on sign-in. The real session cookie
 * is HttpOnly and lives on the API origin, so this middleware can never see it - and
 * must not try to: this check is D14's "do not flash a shell the user cannot use",
 * nothing more. The API refuses unauthenticated requests regardless, so forging the
 * marker buys a redirect to screens whose every request then answers 401.
 */
const SIGNED_IN_HINT = "telagent_signed_in";

/**
 * Reachable signed out. `login` and its children are where signing in happens;
 * `install` runs before any account exists; `screens` is the design gallery.
 * Everything else is the dashboard.
 */
const PUBLIC_SEGMENTS = new Set(["login", "install", "screens"]);

/**
 * Every page lives under a locale segment. A request without one is redirected to
 * the language the browser asks for, falling back to English. A dashboard page
 * visited signed out is redirected to sign-in, carrying the destination so the
 * visitor lands where they were going once they are in.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Exclusions live here, not in the config matcher. The matcher regex silently
  // stopped matching anything but the root under the current Next/Turbopack dev
  // server, which turned both the locale redirect and the sign-in redirect off with
  // no error anywhere. Plain code cannot fail that quietly.
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname.includes(".")
  ) {
    return NextResponse.next();
  }

  const locale = LOCALES.find(
    (candidate) => pathname === `/${candidate}` || pathname.startsWith(`/${candidate}/`),
  );

  if (!locale) {
    const url = request.nextUrl.clone();
    url.pathname = `/${preferredLocale(request)}${pathname === "/" ? "" : pathname}`;
    return NextResponse.redirect(url);
  }

  const segment = pathname.split("/")[2] ?? "";
  const isPublic = segment === "" || PUBLIC_SEGMENTS.has(segment);

  if (!isPublic && !request.cookies.has(SIGNED_IN_HINT)) {
    const url = request.nextUrl.clone();
    url.pathname = `/${locale}/login`;
    // The path only, never the query: a query string can carry tokens and search
    // terms that do not belong in a login URL.
    url.search = `next=${encodeURIComponent(pathname)}`;
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

function preferredLocale(request: NextRequest): string {
  const header = request.headers.get("accept-language") ?? "";
  for (const part of header.split(",")) {
    const tag = part.split(";")[0].trim().toLowerCase();
    const match = LOCALES.find((locale) => tag === locale || tag.startsWith(`${locale}-`));
    if (match) return match;
  }
  return DEFAULT_LOCALE;
}


