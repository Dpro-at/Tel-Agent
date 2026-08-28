"use client";

import Link from "next/link";
import { useState } from "react";

import { BrandMark, brandSlug } from "@/components/brands/brand-mark";
import { Sidebar } from "@/components/shell/sidebar";
import { StatePreview, type ScreenState } from "@/components/state-preview";
import { appsOverview, type AppsOverview, type InstalledApp } from "@/lib/api";
import { CATALOGUE, CATEGORIES, tintFor, type App } from "@/lib/apps/data";
import { interpolate } from "@/lib/i18n";
import { EXTERNAL } from "@/lib/links";
import type { Locale } from "@/lib/locales";
import { useResource, type Resource } from "@/lib/use-resource";

import type { AppsDictionary } from "./page";

/**
 * Store categories where a vendored logo may stand in for a letter.
 *
 * `channels` and `notifications` are covered end to end - every company in them has a
 * mark, so no logo sits beside a lettered square. `sip` is not: Twilio and Telekom
 * CompanyFlex have marks and sipgate, toplink, ecotel and A1 Telekom Austria have none in
 * any registry. It is listed anyway, deliberately, because those four would need marks
 * drawn by hand and a recognisable Twilio is worth more than a uniform row of letters.
 * Add a category here once you have looked at what the row will actually show.
 */
const BRANDED_CATEGORIES = ["channels", "notifications", "sip"];

function Mark({
  id,
  glyph,
  size = 40,
  brand = true,
}: {
  id: string;
  glyph: string;
  size?: number;
  /**
   * Whether a vendored logo may replace the letter here.
   *
   * Off by default in the store, and turned on per category by `BRANDED_CATEGORIES`.
   */
  brand?: boolean;
}) {
  const tint = tintFor(id);

  // A letter is a stand-in for a name. Where the name is a company whose mark is
  // vendored, the mark is what the reader recognises - and it carries the owner's
  // colour rather than one hashed from the id.
  if (brand && brandSlug(id)) return <BrandMark id={id} size={size} />;

  return (
    <span
      className="inline-flex flex-none items-center justify-center rounded-[10px] border font-semibold"
      style={{
        width: size,
        height: size,
        fontSize: size > 38 ? 14.5 : 15,
        borderColor: tint.border,
        background: tint.background,
        color: tint.color,
      }}
    >
      {glyph}
    </span>
  );
}

export function Apps({ locale, t }: { locale: Locale; t: AppsDictionary }) {
  const [state, setState] = useState<ScreenState>("default");
  const [tab, setTab] = useState<"installed" | "store">("installed");
  const [category, setCategory] = useState("all");
  const [packageFile, setPackageFile] = useState<string | null>(null);

  // The installed tab is real: the manifests the registry loaded, and what it
  // refused. The store below stays a drawing — there is nothing to download yet,
  // and its cards say "Planned" rather than pretending otherwise.
  const overview = useResource<AppsOverview>(() => appsOverview());

  const offline = state === "offline";
  const empty = state === "empty";
  const showBody = state === "default" || empty || offline;

  const installedCount =
    overview.data === null
      ? null
      : overview.data.installed.length + overview.data.refused.length;
  const storeApps = CATALOGUE.filter((entry) => entry.install !== "installed");
  const shown = storeApps.filter((entry) => category === "all" || entry.category === category);
  const sections = CATEGORIES.filter((entry) => category === "all" || category === entry.id)
    .map((entry) => ({
      ...entry,
      apps: shown.filter((app) => app.category === entry.id),
    }))
    .filter((section) => section.apps.length > 0);

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="settings" />
      </div>

      <StatePreview state={state} onChange={setState} />

      {offline ? (
        <div className="bg-od-red-bg border-od-red-border flex flex-wrap items-center gap-[14px] border-b px-7 py-4">
          <span
            className="size-[10px] flex-none rounded-full bg-[#F0605E]"
            style={{ animation: "od-ring 1.6s ease-out infinite" }}
          />
          <div className="min-w-[240px] flex-[1_1_340px]">
            <div className="text-[16px] font-semibold text-[color:var(--od-red-text)]">
              {t.offline_title}
            </div>
            <div className="mt-[3px] text-[color:var(--od-red-text-2)]">
              {t.offline_body_before}
              <span className="mono">09:58</span>
              {t.offline_body_after}
            </div>
          </div>
          <button
            type="button"
            className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[9px_15px] font-medium text-[color:var(--od-red-text-3)]"
          >
            {t.offline_retry}
          </button>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1320px] p-[26px_28px_80px]">
        {state === "error" ? <AppCrashed t={t} /> : null}

        {state === "loading" ? (
          <div>
            <div
              className="h-7 w-[180px] rounded-md"
              style={{
                background:
                  "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
                backgroundSize: "420px 100%",
                animation: "od-shimmer 1.4s linear infinite",
              }}
            />
            <div className="mt-6 flex flex-col gap-3">
              {[0, 1, 2].map((index) => (
                <div
                  key={index}
                  className="border-od-raise-12 h-24 rounded-[10px] border"
                  style={{
                    background:
                      "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
                    backgroundSize: "420px 100%",
                    animation: "od-shimmer 1.4s linear infinite",
                  }}
                />
              ))}
            </div>
          </div>
        ) : null}

        {showBody ? (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div className="max-w-[66ch]">
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">{t.title}</h1>
                <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
              </div>
              {/* Installing a package is an action, not a destination, so it is a
                  control rather than a link. Choosing the file needs no server; sending
                  it does, which is why nothing happens past the file name. */}
              <label className="border-od-border-2 text-od-muted hover:text-od-text-2 flex cursor-pointer items-center gap-[10px] rounded-md border p-[9px_15px] text-[13px] whitespace-nowrap">
                <input
                  type="file"
                  accept=".tar.gz,.tgz,.zip"
                  className="sr-only"
                  onChange={(event) => setPackageFile(event.target.files?.[0]?.name ?? null)}
                />
                <span>{t.install_from_file}</span>
                {/* A file name is machine data: verbatim, monospace, left to right. */}
                {packageFile ? (
                  <span dir="ltr" className="mono ltr-data text-od-faint-2 max-w-[24ch] truncate">
                    {packageFile}
                  </span>
                ) : null}
              </label>
            </div>

            <div className="border-od-border mt-[22px] flex flex-wrap gap-1 border-b">
              {(
                [
                  ["installed", "tab_installed", installedCount ?? "…"],
                  ["store", "tab_store", storeApps.length],
                ] as const
              ).map(([id, label, count]) => {
                const on = tab === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setTab(id)}
                    className="-mb-px inline-flex cursor-pointer items-center gap-2 border-none bg-transparent p-[10px_15px] text-[13.5px]"
                    style={{
                      borderBottom: `2px solid ${on ? "var(--od-text)" : "transparent"}`,
                      color: on ? "var(--od-text)" : "var(--od-muted-4)",
                      fontWeight: on ? 600 : 400,
                    }}
                  >
                    <span>{t[label]}</span>
                    <span
                      dir="ltr"
                      className="mono border-od-line rounded-full border p-[1px_7px] text-[11.5px]"
                      style={{
                        background: on ? "var(--od-raise-7)" : "var(--od-panel-deep-3)",
                        color: on ? "var(--od-text-3)" : "var(--od-faint)",
                      }}
                    >
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>

            {tab === "installed" ? (
              <InstalledTab t={t} overview={overview} onBrowse={() => setTab("store")} />
            ) : null}

            {tab === "store" ? (
              <section className="mt-[22px]">
                <div className="mb-[14px] flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px]">
                  <p className="text-od-muted-4 m-0 max-w-[62ch] text-[13px] text-pretty">{t.store_note}</p>
                  <span className="text-od-faint text-[12.5px]">
                    {interpolate(t.store_count, { shown: shown.length, total: storeApps.length })}
                  </span>
                </div>

                {/* The point of the whole catalogue: nobody waits on us to write a connector. */}
                <div className="mb-4 flex flex-wrap items-center justify-between gap-x-5 gap-y-3 rounded-[10px] border border-[color:var(--od-violet-border)] bg-[rgba(139,124,255,.06)] p-[14px_16px]">
                  <div className="min-w-[280px] flex-[1_1_400px]">
                    <div className="text-od-text font-semibold">{t.mcp_title}</div>
                    <div className="text-od-muted-2 mt-1 max-w-[78ch] text-[13px] text-pretty">
                      {t.mcp_body_before}
                      <span className="font-medium text-[color:var(--od-violet-3)]">{t.install_mcp}</span>
                      {t.mcp_body_after}
                    </div>
                  </div>
                  <Link
                    href={`/${locale}/connectors`}
                    className="text-od-violet text-[13px] whitespace-nowrap hover:underline"
                  >
                    {t.mcp_link}
                  </Link>
                </div>

                <div className="mb-[14px] flex flex-wrap items-center gap-x-[14px] gap-y-[10px]">
                  <div className="border-od-border-6 bg-od-canvas-2 flex min-w-[220px] flex-[1_1_260px] items-center gap-[9px] rounded-lg border p-[9px_13px]">
                    <span className="text-od-faint-2 text-[13px] leading-none">⌕</span>
                    <span className="text-od-faint-2 text-[13.5px]">
                      {interpolate(t.search_placeholder, { total: CATALOGUE.length })}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {[{ id: "all", label: "filter_all" as const }, ...CATEGORIES].map((entry) => (
                      <button
                        key={entry.id}
                        type="button"
                        onClick={() => setCategory(entry.id)}
                        className={`cursor-pointer rounded-full border p-[6px_12px] text-[13px] whitespace-nowrap ${
                          category === entry.id
                            ? "border-od-stroke bg-od-line-2 text-od-text"
                            : "border-od-border-7 bg-od-panel-deep-3 text-od-muted-4"
                        }`}
                      >
                        {t[entry.label]}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex flex-col gap-[26px]">
                  {sections.map((section) => (
                    <div key={section.id}>
                      <div className="border-od-border mb-[14px] flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px] border-b pb-[10px]">
                        <h2 className="text-od-text m-0 text-[16px] font-semibold">{t[section.label]}</h2>
                        <span className="text-od-faint text-[12.5px] text-pretty">{t[section.note]}</span>
                      </div>
                      <div
                        className="grid items-stretch gap-[14px]"
                        style={{ gridTemplateColumns: "repeat(auto-fill, minmax(268px, 1fr))" }}
                      >
                        {section.apps.map((entry) => (
                          <AppCard key={entry.id} t={t} app={entry} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}

            <div className="border-od-line bg-od-panel-deep-2 mt-6 flex flex-wrap items-center justify-between gap-x-5 gap-y-3 rounded-[10px] border p-[14px_16px]">
              <div className="max-w-[74ch] min-w-0">
                <div className="text-od-text-5 font-medium">{t.write_title}</div>
                <div className="text-od-muted-5 mt-[3px] text-[13px] text-pretty">{t.write_body}</div>
              </div>
              <a
                href={EXTERNAL.docs}
                target="_blank"
                rel="noreferrer"
                className="text-od-violet text-[13px] hover:underline"
              >
                {t.write_link}
              </a>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * The installed tab, wired to `/api/apps` — the manifests the registry actually
 * loaded, and what it refused at start.
 *
 * What went, and why: the fixture rows claiming WhatsApp and Telegram were installed
 * were drawings of extensions that do not exist, and their Settings / Deactivate /
 * Delete links had no endpoint behind them. A control with no endpoint is removed,
 * not drawn — the write half arrives when the runtime consults per-workspace
 * enablement, which nothing does yet.
 */
function InstalledTab({
  t,
  overview,
  onBrowse,
}: {
  t: AppsDictionary;
  overview: Resource<AppsOverview>;
  onBrowse: () => void;
}) {
  if (overview.data === null && overview.loading) {
    return (
      <section className="mt-[22px] flex flex-col gap-3">
        {[0, 1, 2].map((index) => (
          <div
            key={index}
            className="border-od-raise-12 h-24 rounded-[10px] border"
            style={{
              background:
                "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
              backgroundSize: "420px 100%",
              animation: "od-shimmer 1.4s linear infinite",
            }}
          />
        ))}
      </section>
    );
  }

  if (overview.data === null) {
    return (
      <section className="border-od-line bg-od-panel-deep-3 mt-[22px] rounded-[10px] border p-[18px]">
        <p className="m-0 text-[13px] text-[color:var(--od-red-text-6)]">
          {overview.error?.message ?? t.installed_failed}
        </p>
        <button
          type="button"
          onClick={overview.reload}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-3 cursor-pointer rounded-[7px] border p-[7px_13px] text-[12.5px]"
        >
          {t.installed_retry}
        </button>
      </section>
    );
  }

  const { installed, refused } = overview.data;

  if (installed.length === 0 && refused.length === 0) {
    return (
      <section className="border-od-border-6 bg-od-panel-deep-2 mt-[22px] rounded-[10px] border border-dashed p-[34px_28px]">
        <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
        <p className="text-od-muted mt-[10px] max-w-[60ch] text-pretty">{t.empty_body}</p>
        <button
          type="button"
          onClick={onBrowse}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-[18px] cursor-pointer rounded-md border p-[9px_16px] font-medium"
        >
          {t.empty_browse}
        </button>
      </section>
    );
  }

  return (
    <section className="mt-[22px] flex flex-col gap-3">
      {installed.map((entry) => (
        <InstalledRow key={entry.slug} t={t} entry={entry} />
      ))}

      {refused.length > 0 ? (
        <div className="mt-2">
          <div className="border-od-border mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-[10px] border-b pb-[10px]">
            <h2 className="text-od-text m-0 text-[16px] font-semibold">{t.refused_title}</h2>
            <span className="text-od-faint text-[12.5px] text-pretty">{t.refused_note}</span>
          </div>
          <div className="flex flex-col gap-3">
            {refused.map((entry) => (
              <div
                key={entry.slug}
                className="border-od-red-border-3 bg-od-red-bg-4 flex flex-wrap items-center gap-x-[18px] gap-y-3 rounded-[10px] border p-4"
              >
                <div className="min-w-[220px] flex-[1_1_280px]">
                  <div className="flex flex-wrap items-center gap-[9px]">
                    {/* A refused module has no manifest to name it - the module path
                        is all that is known, and it is machine data. */}
                    <span dir="ltr" className="mono ltr-data text-od-text text-[14px] font-semibold">
                      {entry.slug}
                    </span>
                    <span className="border-od-red-border bg-od-red-bg-5 rounded-md border p-[2px_9px] text-[12px] font-medium text-[color:var(--od-red-text-5)]">
                      {t.refused_badge}
                    </span>
                  </div>
                  <div
                    dir="ltr"
                    className="mono ltr-data text-od-muted-5 mt-[6px] text-start text-[12.5px] [overflow-wrap:anywhere]"
                  >
                    {entry.reason}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

function InstalledRow({ t, entry }: { t: AppsDictionary; entry: InstalledApp }) {
  // The catalogue's presentation for the same slug: the drawn mark and, for our own
  // applications, the name and description as copy in the reader's language. A slug
  // the catalogue does not know keeps its manifest's own words verbatim - a community
  // extension describes itself, and its words are content, not our copy.
  const drawn = CATALOGUE.find((app) => app.id === entry.slug);
  const categoryLabel = CATEGORIES.find((section) => section.id === entry.category)?.label;
  const originKey =
    entry.origin in ORIGIN_LABEL ? ORIGIN_LABEL[entry.origin as keyof typeof ORIGIN_LABEL] : null;

  return (
    <div className="border-od-line bg-od-panel-deep-3 flex flex-wrap items-start gap-x-5 gap-y-[14px] rounded-[10px] border p-4">
      <Mark
        id={entry.slug}
        glyph={drawn?.mark ?? entry.slug.slice(0, 2)}
        size={38}
        brand={BRANDED_CATEGORIES.includes(entry.category)}
      />
      <div className="min-w-[240px] flex-[1_1_300px]">
        <div className="flex flex-wrap items-center gap-[10px]">
          {drawn?.name ? (
            <span className="text-od-text text-[16px] font-semibold">{t[drawn.name]}</span>
          ) : (
            <span dir="ltr" className="text-od-text text-start text-[16px] font-semibold">
              {entry.name}
            </span>
          )}
          <span
            className="rounded-md border p-[2px_9px] text-[12px] font-medium whitespace-nowrap"
            style={{
              borderColor: entry.running ? "var(--od-green-border)" : "var(--od-border-7)",
              background: entry.running ? "rgba(63,185,132,.11)" : "var(--od-raise-5)",
              color: entry.running ? "var(--od-green-text)" : "var(--od-faint)",
            }}
          >
            {entry.running ? t.active : t.inactive}
          </span>
        </div>
        <div className="text-od-faint mt-1 text-[12.5px]">
          {categoryLabel ? <span>{t[categoryLabel]}</span> : null}
          {originKey ? <span>{`${categoryLabel ? " · " : ""}${t[originKey]}`}</span> : null}
          {entry.version ? (
            <>
              {" · "}
              <span dir="ltr" className="mono ltr-data">
                {entry.version}
              </span>
            </>
          ) : null}
        </div>
        <div className="text-od-muted-5 mt-[6px] text-[13px] text-pretty">
          {drawn?.desc ? (
            t[drawn.desc]
          ) : (
            <span dir="ltr" className="block text-start">
              {entry.description}
            </span>
          )}
        </div>
        {entry.scopes.length > 0 ? (
          <div className="text-od-faint mt-[6px] text-[12px]">
            <span>{t.asks_for}</span>{" "}
            <span dir="ltr" className="mono ltr-data">
              {entry.scopes.join(" · ")}
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}

const INSTALL_LABEL = {
  installed: "install_installed",
  install: "install_install",
  planned: "install_planned",
  mcp: "install_mcp",
} as const;

const ORIGIN_LABEL = {
  official: "origin_official",
  community: "origin_community",
  planned: "origin_planned",
  mcp: "origin_mcp",
} as const;

function AppCard({ t, app }: { t: AppsDictionary; app: App }) {
  const planned = app.install === "planned";
  const viaMcp = app.install === "mcp";

  return (
    <div
      className="flex min-w-0 flex-col gap-[10px] rounded-[10px] p-4"
      style={{
        border: planned ? "1px dashed var(--od-border-7)" : "1px solid var(--od-line)",
        background: planned ? "transparent" : "var(--od-panel-deep-3)",
      }}
    >
      <div className="flex flex-wrap items-start gap-3">
        <Mark id={app.id} glyph={app.mark} brand={BRANDED_CATEGORIES.includes(app.category)} />
        <div className="min-w-[140px] flex-[1_1_160px]">
          <div className="text-od-text text-[15px] font-semibold text-pretty">
            {app.name ? t[app.name] : app.nameText}
          </div>
          {/* Category and origin are copy; the version and size are not. */}
          <div className="text-od-faint mt-[3px] text-[11.5px]">
            {`${t[CATEGORIES.find((entry) => entry.id === app.category)!.label]} · ${t[ORIGIN_LABEL[app.origin]]}`}
            {app.eta ? ` · ${t[app.eta]}` : null}
            {app.version ? (
              <>
                {" · "}
                <span dir="ltr" className="mono ltr-data">
                  {app.version}
                </span>
              </>
            ) : null}
          </div>
        </div>
      </div>

      <div className="text-od-muted-2 text-[13px] text-pretty">{t[app.desc]}</div>

      {app.warn ? (
        <div className="text-[12.5px] text-pretty text-[color:var(--od-amber-text)]">{t[app.warn]}</div>
      ) : null}

      <button
        type="button"
        disabled={planned}
        className="mt-auto w-full rounded-[7px] p-[9px_13px] text-[13px] font-medium whitespace-nowrap"
        style={{
          cursor: planned ? "default" : "pointer",
          border: viaMcp
            ? "1px solid var(--od-violet-border)"
            : planned
              ? "1px solid transparent"
              : "1px solid var(--od-stroke)",
          background: viaMcp
            ? "rgba(139,124,255,.10)"
            : planned
              ? "var(--od-raise-4)"
              : "var(--od-raise-10)",
          color: viaMcp
            ? "var(--od-violet-3)"
            : planned
              ? "var(--od-faint)"
              : "var(--od-text-2)",
        }}
      >
        {t[INSTALL_LABEL[app.install]]}
      </button>
    </div>
  );
}

function AppCrashed({ t }: { t: AppsDictionary }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <div className="border-od-red-border bg-od-red-bg inline-flex items-center gap-2 rounded-md border p-[5px_10px] text-[12px] font-semibold text-[color:var(--od-red-text)]">
          {t.error_label}
        </div>
        <h2 className="mt-[18px] mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">{t.error_body}</p>
        <div
          dir="ltr"
          className="border-od-border-2 bg-od-canvas-2 mono ltr-data text-od-text-5 mt-[18px] rounded-lg border p-[12px_14px] text-[12.5px]"
        >
          telegram-channel v0.9.1 —{" "}
          <span className="text-[color:var(--od-red-text-5)]">{t.error_exit}</span>
        </div>
        <div className="mt-5 flex flex-wrap gap-[10px]">
          <button
            type="button"
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-md border p-[9px_16px] font-medium"
          >
            {t.error_disable}
          </button>
          <button
            type="button"
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[9px_16px]"
          >
            {t.error_log}
          </button>
        </div>
      </div>
    </div>
  );
}
