"use client";

import Link, { type LinkProps } from "next/link";

import { INSTALLATION } from "./installation";

/**
 * The frame every screen in the sign-in flow shares: the brand, the host it is
 * signing in to, a single centred column, and a footer line.
 *
 * `max-width`, never a fixed width - German runs ~30% longer than English.
 */
export function AuthFrame({
  children,
  banner,
  footer,
}: {
  children: React.ReactNode;
  /** An offline or error strip that sits above the column, full width. */
  banner?: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="bg-od-canvas text-od-text-2 flex min-h-dvh flex-col">
      {banner}

      <div className="flex flex-1 items-center justify-center px-7 pt-[60px] pb-[90px]">
        <div className="w-full max-w-[420px]">
          <div className="flex flex-wrap items-baseline gap-[10px]">
            <div className="text-od-text text-[20px] font-semibold tracking-[-0.01em]">
              Tel-Agent
            </div>
            <span className="mono ltr-data text-od-faint-2 text-[12px]">{INSTALLATION.version}</span>
          </div>
          {/* The block follows the page direction; only the hostname itself is forced
              LTR, so in Arabic it still starts at the right edge like everything else. */}
          <div className="text-od-muted-5 mt-[6px] text-[12.5px] [overflow-wrap:anywhere]">
            <span className="mono ltr-data">{INSTALLATION.host}</span>
          </div>

          {children}

          {footer ? (
            <div className="text-od-faint-2 mt-5 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[12.5px]">
              {footer}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** The card body shared by the flow - same border, fill and padding as the sign-in card. */
export function AuthCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-od-line bg-od-panel-deep-3 mt-[26px] rounded-xl border p-[26px]">
      {children}
    </div>
  );
}

/** A field's input, in the one style the flow uses. `invalid` reddens the border. */
export function authInputClass(invalid = false) {
  return [
    "mt-2 w-full rounded-lg border px-[13px] py-[11px] text-[15px] outline-none ltr-data",
    "bg-od-canvas-2 text-od-text-2 focus:border-od-violet",
    invalid ? "border-od-red-border-2" : "border-od-border-6",
  ].join(" ");
}

/** The primary button, and the disabled treatment used when the server is unreachable. */
export function authButtonClass(disabled: boolean) {
  return [
    "mt-1 w-full rounded-lg border p-3 text-[15px] font-semibold whitespace-normal",
    disabled
      ? "border-od-border-6 bg-od-raise text-od-faint-2 cursor-not-allowed"
      : "border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer",
  ].join(" ");
}

/**
 * The "cannot reach your server" strip. It is the same in every screen of the flow,
 * and it says which host failed - so the hostname is passed already assembled.
 */
export function OfflineBanner({
  title,
  body,
  retry,
}: {
  title: string;
  body: React.ReactNode;
  retry: string;
}) {
  return (
    <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-4">
      <span
        className="size-[10px] flex-none rounded-full bg-[#F0605E]"
        style={{ animation: "od-ring 1.6s ease-out infinite" }}
      />
      <div className="min-w-[240px] flex-[1_1_340px]">
        <div className="text-od-red-text text-base font-semibold">{title}</div>
        <div className="text-od-red-text-2 mt-[3px]">{body}</div>
      </div>
      <button
        type="button"
        className="border-od-red-border-2 bg-od-red-bg-2 text-od-red-text-3 hover:bg-od-red-bg-3 cursor-pointer rounded-md border px-[15px] py-[9px] font-medium"
      >
        {retry}
      </button>
    </div>
  );
}

/**
 * Splits a translated sentence on a placeholder so the value can be rendered as its
 * own element. A hostname is machine data: monospace, and left to right even in Arabic.
 */
export function withMachineValue(template: string, token: string, value: React.ReactNode) {
  const [before, after = ""] = template.split(`{${token}}`);
  return (
    <>
      {before}
      <span className="mono ltr-data">{value}</span>
      {after}
    </>
  );
}

/**
 * The flow's primary action.
 *
 * There is no `api/` yet, so the action navigates rather than submits - the flow has to
 * be walkable for review. When it cannot proceed it is a disabled button, not a dimmed
 * link: a link that goes nowhere is still focusable, still followable by keyboard, and
 * still a link to anything reading the page.
 */
export function AuthAction<RouteType>({
  href,
  disabled,
  className = "",
  children,
}: {
  /* Generic over the route so `typedRoutes` still checks the destination through here. */
  href: LinkProps<RouteType>["href"];
  disabled: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  if (disabled) {
    return (
      <button type="button" disabled className={`${authButtonClass(true)} ${className}`}>
        {children}
      </button>
    );
  }

  return (
    <Link href={href} className={`${authButtonClass(false)} ${className} block text-center`}>
      {children}
    </Link>
  );
}

/**
 * The flow's primary action as a real submit button.
 *
 * `AuthAction` above navigates - it existed so the flow was walkable before `api/`
 * did. Screens that actually submit use this instead, inside a `<form>`, so Enter in
 * any field submits and the browser's own form semantics apply.
 */
export function AuthSubmit({
  disabled,
  className = "",
  children,
}: {
  disabled: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <button type="submit" disabled={disabled} className={`${authButtonClass(disabled)} ${className}`}>
      {children}
    </button>
  );
}
