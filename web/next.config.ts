import path from "node:path";
import type { NextConfig } from "next";

// Where the dashboard's own requests go. The API is a separate origin - a different
// port in development, often a different host behind a reverse proxy - so `connect-src`
// has to name it or every request the dashboard makes is blocked by its own policy.
// This is the line that makes a CSP here worth testing in a browser rather than
// asserting: get it wrong and the screens render perfectly and load nothing.
const API_ORIGIN = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * G1. What this page is allowed to load, and who may frame it.
 *
 * `'unsafe-inline'` on scripts is not a shortcut taken for convenience. The honest
 * alternative is a nonce, and a nonce has to be generated per request - which means
 * every page becomes dynamically rendered, and this app is deliberately prerendered
 * static across three locales. The inline scripts it covers are the ones the framework
 * emits to hydrate; the application adds none of its own. Styles need it for the same
 * reason and one more: the utility classes compile to a stylesheet, but a handful of
 * computed colours are set as inline style attributes.
 *
 * Development additionally needs `'unsafe-eval'`, which the refresh runtime uses. It is
 * added only there - shipping it would give away most of what the policy is for.
 */
function contentSecurityPolicy(): string {
  const development = process.env.NODE_ENV !== "production";
  return [
    "default-src 'self'",
    `script-src 'self' 'unsafe-inline'${development ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    // `data:` covers the inlined icons; `blob:` the object URLs a download builds.
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self' ${API_ORIGIN}${development ? " ws: wss:" : ""}`,
    // The dashboard is never framed by anything. The widget is the embeddable
    // surface, and it is served by the API with a policy of its own.
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join("; ");
}

const config: NextConfig = {
  reactStrictMode: true,
  // The Docker image runs `node server.js` from this bundle: the server plus only the
  // files it traces, instead of the whole node_modules tree. Harmless in development,
  // which never reads it.
  output: "standalone",
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy() },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          // Belt and braces with `frame-ancestors` above, for browsers that honour
          // only this one.
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
  // On now that every screen in `docs/SPEC.md` §A6 exists: a link to a route that is
  // not there is a type error rather than a 404 found by clicking.
  typedRoutes: true,
  // The UI dictionaries live in the repository-wide `locales/` directory, one level
  // above this app, so the workspace root is the repository - not `web/`.
  turbopack: { root: path.join(__dirname, "..") },
  outputFileTracingRoot: path.join(__dirname, ".."),
};

export default config;
