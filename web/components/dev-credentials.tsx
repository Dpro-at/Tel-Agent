"use client";

import { useState } from "react";

/**
 * Development-only: fill the sign-in form with a seeded account.
 *
 * Temporary, and it says so on screen. It exists so that permissions can be exercised
 * against a real account per role instead of being reasoned about, and it is deleted
 * once there is a better way to switch roles while testing.
 *
 * **It renders nothing outside development.** The check is on `process.env.NODE_ENV`,
 * which Next.js replaces with a literal at build time — so in a production build the
 * branch is `false` and the whole component, the account list included, is removed by
 * the bundler rather than merely hidden. A conditional on a runtime value would ship
 * these usernames to every visitor.
 */

const PASSWORD = "development-only-password";

/** Mirrors `PEOPLE` in `scripts/seed.py`. */
const ACCOUNTS = [
  { username: "wagner", role: "Owner", note: "everything, cannot be removed" },
  { username: "mohamed", role: "Admin", note: "everything except deleting the workspace" },
  { username: "sabine", role: "Reception", note: "takes calls, edits contacts" },
  { username: "lukas", role: "Read only", note: "reads calls, changes nothing" },
  { username: "julia", role: "Invited", note: "has not accepted yet" },
];

function fill(username: string) {
  const form = document.querySelector("form") ?? document;
  const user = form.querySelector<HTMLInputElement>("#username");
  const password = form.querySelector<HTMLInputElement>("#password");

  for (const [field, value] of [
    [user, username],
    [password, PASSWORD],
  ] as const) {
    if (!field) continue;
    // Assigning `.value` directly does not tell React the input changed, so a
    // controlled field would snap back to its old value on the next render. Going
    // through the native setter and dispatching `input` is what React listens for.
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value",
    )?.set;
    setter?.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

export function DevCredentials() {
  const [open, setOpen] = useState(false);

  if (process.env.NODE_ENV !== "development") return null;

  return (
    <div className="border-od-border-6 bg-od-panel-deep-2 mt-4 rounded-xl border border-dashed p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-od-text-3 text-[13px] font-semibold">Development sign-in</div>
          <div className="text-od-faint mt-[3px] text-[12px]">
            Seeded accounts, one per role. Removed before release.
          </div>
        </div>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-lg border px-[13px] py-2 text-[13px] font-medium"
        >
          {open ? "Hide" : "Fill in a user"}
        </button>
      </div>

      {open ? (
        <div className="mt-3 flex flex-col gap-[6px]">
          {ACCOUNTS.map((account) => (
            <button
              key={account.username}
              type="button"
              onClick={() => fill(account.username)}
              className="border-od-border-2 hover:bg-od-raise flex w-full cursor-pointer items-baseline justify-between gap-3 rounded-lg border px-[12px] py-[9px] text-start"
            >
              <span className="mono ltr-data text-od-text text-[13px]">
                {account.username}
              </span>
              <span className="text-od-muted-5 min-w-0 flex-1 truncate text-[12px]">
                {account.note}
              </span>
              <span className="text-od-violet-3 flex-none text-[12px]">{account.role}</span>
            </button>
          ))}
          <div className="text-od-faint mt-1 text-[11.5px]">
            Every one of them uses the password{" "}
            <span className="mono ltr-data">{PASSWORD}</span>.
          </div>
        </div>
      ) : null}
    </div>
  );
}
