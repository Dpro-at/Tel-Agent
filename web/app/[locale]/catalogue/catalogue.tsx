"use client";

import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  addService,
  changeService,
  catalogue as loadCatalogue,
  removeService,
  type Catalogue as CatalogueData,
  type PriceMode,
  type Service,
  type ServiceDraft,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { CatalogueDictionary } from "./page";

type Key = keyof CatalogueDictionary;

const SERVICE_COLUMNS = "minmax(0,1.8fr) minmax(0,.8fr) minmax(0,.9fr) minmax(0,1.1fr) 104px 84px";

const TABS: { id: string; label: Key }[] = [
  { id: "services", label: "tab_services" },
  { id: "fields", label: "tab_fields" },
  { id: "preview", label: "tab_preview" },
];

const LENGTH_CHOICES = [20, 30, 45, 60, 90];

const PRICE_MODES: { id: PriceMode; label: Key }[] = [
  { id: "fixed", label: "price_fixed" },
  { id: "hourly", label: "price_hourly" },
  { id: "on_request", label: "price_on_request" },
];

/** One micro is a millionth, which is what the column stores. */
const MICROS = 1_000_000;

/**
 * A typed amount to integer micros.
 *
 * Both separators are accepted because both are typed: a German keyboard produces
 * "120,50" and an English one "120.50", and refusing either would be refusing the
 * price rather than reading it. Returns null when there is no number in there at all.
 */
function toMicros(typed: string): number | null {
  const cleaned = typed.replace(/\s/g, "").replace(",", ".");
  if (cleaned === "") return null;
  const amount = Number(cleaned);
  if (!Number.isFinite(amount) || amount < 0) return null;
  // Rounded, not truncated: 120.555 is meant as a price, and floating point cannot
  // hold it exactly - `Math.round` is what keeps the stored micros the nearest one.
  return Math.round(amount * MICROS);
}

/** The stored micros as money, in the reader's locale and the workspace's currency. */
function money(micros: number, currency: string, locale: Locale): string {
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(micros / MICROS);
  } catch {
    // An unknown currency code is a setting somebody typed, not a reason to show
    // nothing: the number is still the useful half.
    return `${micros / MICROS} ${currency}`;
  }
}

/**
 * The amount and the words about it, kept apart.
 *
 * One string would have to be given one direction, and "€120.50 / hourly rate" has
 * two: the figure is always left to right, and the phrase belongs to the page. Joined
 * and forced to `ltr`, the Arabic half is laid out backwards inside its own sentence.
 */
function priceOf(service: Service, currency: string, locale: Locale, t: CatalogueDictionary) {
  if (service.price_mode === "on_request" || service.price_micros === null) {
    return { amount: null, words: t.price_on_request };
  }
  return {
    amount: money(service.price_micros, currency, locale),
    words: service.price_mode === "hourly" ? t.price_hourly : null,
  };
}

function Switch({ on, onClick, busy }: { on: boolean; onClick: () => void; busy?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={busy}
      onClick={onClick}
      className="relative h-[21px] w-[38px] flex-none cursor-pointer rounded-full border transition-colors disabled:opacity-50"
      style={{
        borderColor: on ? "var(--od-violet-border)" : "var(--od-border-7)",
        background: on ? "var(--od-violet)" : "var(--od-canvas-2)",
      }}
    >
      <span
        className="absolute top-[2px] size-[15px] rounded-full transition-[inset-inline-start]"
        style={{
          insetInlineStart: on ? "19px" : "2px",
          background: on ? "#fff" : "var(--od-faint-2)",
        }}
      />
    </button>
  );
}

function Chip({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer rounded-[7px] border p-[7px_12px] text-[13px] whitespace-nowrap"
      style={{
        borderColor: on ? "var(--od-border-9)" : "var(--od-border-4)",
        background: on ? "var(--od-raise-7)" : "transparent",
        color: on ? "var(--od-text)" : "var(--od-muted-4)",
        fontWeight: on ? 500 : 400,
      }}
    >
      {label}
    </button>
  );
}

export function Catalogue({ locale, t }: { locale: Locale; t: CatalogueDictionary }) {
  const [tab, setTab] = useState("services");
  const [newOpen, setNewOpen] = useState(false);
  // Which row is mid-request. One at a time is enough: the only inline action is a
  // switch, and disabling the row it belongs to says which one is being saved.
  const [busy, setBusy] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);

  const data = useResource<CatalogueData>(() => loadCatalogue());

  const offline = data.error?.kind === "offline";
  const refused = data.error !== null && data.error.kind !== "offline";
  const services = data.data?.services ?? [];
  const currency = data.data?.currency ?? "EUR";

  async function act(id: number, run: () => Promise<unknown>) {
    setBusy(id);
    setFailed(false);
    try {
      await run();
      data.reload();
    } catch {
      // Nothing local was changed, so there is nothing to roll back - the row still
      // shows what the server last said, which is what is actually stored.
      setFailed(true);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="settings" />
      </div>

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
            <div className="mt-[3px] text-[color:var(--od-red-text-2)]">{t.offline_body}</div>
          </div>
          <button
            type="button"
            onClick={() => data.reload()}
            className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[9px_15px] font-medium text-[color:var(--od-red-text-3)]"
          >
            {t.error_retry}
          </button>
        </div>
      ) : null}

      <div className="mx-auto max-w-[1400px] p-[26px_28px_90px]">
        {refused ? (
          <ServerError t={t} onRetry={() => data.reload()} />
        ) : data.data === null ? (
          <CatalogueSkeleton />
        ) : (
          <div>
            <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
              <div className="max-w-[66ch]">
                <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
                  {t.title}
                </h1>
                <p className="text-od-muted-4 mt-[6px] text-pretty">{t.intro}</p>
              </div>
              <button
                type="button"
                onClick={() => setNewOpen(true)}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-[7px] border p-[9px_15px] font-medium"
              >
                {t.new_service}
              </button>
            </div>

            <div className="border-od-border-2 bg-od-panel mt-5 flex w-max max-w-full flex-wrap gap-[6px] rounded-[10px] border p-[5px]">
              {TABS.map(({ id, label }) => {
                const on = tab === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setTab(id)}
                    className="cursor-pointer rounded-[7px] border p-[8px_14px] text-[13.5px] whitespace-nowrap"
                    style={{
                      borderColor: on ? "var(--od-border-9)" : "transparent",
                      background: on ? "var(--od-raise-7)" : "transparent",
                      color: on ? "var(--od-text)" : "var(--od-muted-4)",
                      fontWeight: on ? 500 : 400,
                    }}
                  >
                    {t[label]}
                  </button>
                );
              })}
            </div>

            {failed ? (
              <div
                role="alert"
                className="border-od-red-border bg-od-red-bg mt-[14px] rounded-[9px] border p-[11px_15px] text-[13px] text-[color:var(--od-red-text-2)]"
              >
                {t.save_failed}
              </div>
            ) : null}

            {tab === "services" ? (
              services.length === 0 ? (
                <div className="border-od-border-6 bg-od-panel-deep-2 mt-[18px] rounded-[10px] border border-dashed p-[46px_30px] text-center">
                  <h3 className="text-od-text m-0 text-[19px] font-semibold">
                    {t.empty_services_title}
                  </h3>
                  <p className="text-od-muted mx-auto mt-[10px] max-w-[56ch] text-pretty">
                    {t.empty_services_body}
                  </p>
                  <button
                    type="button"
                    onClick={() => setNewOpen(true)}
                    className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-5 cursor-pointer rounded-md border p-[9px_16px] font-medium"
                  >
                    {t.empty_services_add}
                  </button>
                </div>
              ) : (
                <div>
                  <div className="border-od-line bg-od-panel-deep-3 mt-[18px] overflow-x-auto overflow-y-hidden rounded-[10px] border">
                    <div
                      className="border-od-line bg-od-canvas-2 text-od-faint grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
                      style={{ gridTemplateColumns: SERVICE_COLUMNS }}
                    >
                      <span>{t.column_service}</span>
                      <span>{t.column_length}</span>
                      <span>{t.column_price}</span>
                      <span>{t.column_who}</span>
                      <span>{t.column_bookable}</span>
                      <span />
                    </div>

                    {services.map((service) => {
                      const price = priceOf(service, currency, locale, t);
                      const working = busy === service.id;
                      return (
                        <div
                          key={service.id}
                          className="hover:bg-od-raise grid items-start gap-[18px] border-b border-[color:var(--od-raise-6)] p-[14px_18px]"
                          style={{
                            gridTemplateColumns: SERVICE_COLUMNS,
                            opacity: service.bookable ? 1 : 0.6,
                          }}
                        >
                          {/* The business's own words, in whatever script they wrote
                              them in - so the direction comes off the text. */}
                          <div dir="auto" className="min-w-0">
                            <div className="text-od-text font-medium text-pretty">
                              {service.name}
                            </div>
                            {service.says ? (
                              <div className="text-od-muted-5 mt-[3px] text-[12.5px] text-pretty">
                                {service.says}
                              </div>
                            ) : null}
                          </div>
                          <div className="text-od-text-5 text-[13px]">
                            {service.minutes
                              ? interpolate(t.minutes, { count: service.minutes })
                              : "—"}
                          </div>
                          <div className="text-od-text-5 text-[13px]">
                            {price.amount ? (
                              <span dir="ltr" className="mono ltr-data inline-block text-start">
                                {price.amount}
                              </span>
                            ) : null}
                            {price.words ? (
                              <span className={price.amount ? "ms-[6px]" : undefined}>
                                {price.words}
                              </span>
                            ) : null}
                          </div>
                          <div dir="auto" className="text-od-text-5 min-w-0 text-pretty">
                            {service.performed_by ?? t.who_any}
                          </div>
                          <div>
                            <Switch
                              on={service.bookable}
                              busy={working}
                              onClick={() =>
                                act(service.id, () =>
                                  changeService(service.id, { bookable: !service.bookable }),
                                )
                              }
                            />
                          </div>
                          <div>
                            <button
                              type="button"
                              disabled={working}
                              onClick={() => {
                                // A price the assistant quotes disappears the moment
                                // this returns, so it asks first.
                                if (
                                  !window.confirm(
                                    interpolate(t.confirm_remove, { name: service.name }),
                                  )
                                ) {
                                  return;
                                }
                                void act(service.id, () => removeService(service.id));
                              }}
                              className="text-od-muted-5 hover:text-od-text-2 cursor-pointer border-none bg-transparent p-0 text-[13px] underline disabled:opacity-50"
                            >
                              {working ? t.saving : t.remove_service}
                            </button>
                          </div>
                        </div>
                      );
                    })}

                    <button
                      type="button"
                      onClick={() => setNewOpen(true)}
                      className="bg-od-canvas-2 text-od-muted-5 hover:bg-od-raise hover:text-od-text-2 w-full cursor-pointer border-none p-[13px_18px] text-start text-[13px]"
                    >
                      {t.add_service}
                    </button>
                  </div>

                  <div className="border-od-border-4 bg-od-panel-deep-4 text-od-muted-5 mt-[14px] rounded-[9px] border p-[13px_16px] text-[12.5px] text-pretty">
                    {t.services_note}
                  </div>
                </div>
              )
            ) : null}

            {/* The other two tabs say what they are waiting for rather than showing
                invented contact fields and invented sentences generated from them. */}
            {tab === "fields" ? <Waiting body={t.fields_note} note={t.fields_waiting} /> : null}
            {tab === "preview" ? (
              <Waiting body={t.empty_preview_body} note={t.preview_waiting} />
            ) : null}
          </div>
        )}
      </div>

      {newOpen ? (
        <NewServiceDialog
          t={t}
          currency={currency}
          onClose={() => setNewOpen(false)}
          onSaved={() => {
            setNewOpen(false);
            data.reload();
          }}
        />
      ) : null}
    </div>
  );
}

function Waiting({ body, note }: { body: string; note: string }) {
  return (
    <div className="border-od-border-6 bg-od-panel-deep-2 mt-[18px] rounded-[10px] border border-dashed p-[36px_30px]">
      <p className="text-od-muted m-0 max-w-[68ch] text-pretty">{body}</p>
      <p className="text-od-faint mt-3 mb-0 max-w-[68ch] text-[13px] text-pretty">{note}</p>
    </div>
  );
}

function NewServiceDialog({
  t,
  currency,
  onClose,
  onSaved,
}: {
  t: CatalogueDictionary;
  currency: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [says, setSays] = useState("");
  const [who, setWho] = useState("");
  const [length, setLength] = useState<number | null>(60);
  const [mode, setMode] = useState<PriceMode>("fixed");
  const [amount, setAmount] = useState("");
  const [bookable, setBookable] = useState(true);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const micros = toMicros(amount);
  // The save button is off until the request would be accepted, so the refusal is a
  // disabled button rather than a round trip that comes back with an error.
  const ready = name.trim() !== "" && (mode === "on_request" || micros !== null);

  async function save() {
    setSaving(true);
    setFailed(false);
    try {
      const draft: ServiceDraft = {
        name: name.trim(),
        says: says.trim() || null,
        minutes: length,
        price_mode: mode,
        price_micros: mode === "on_request" ? null : micros,
        performed_by: who.trim() || null,
        bookable,
      };
      await addService(draft);
      onSaved();
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center overflow-auto p-[40px_20px]"
      style={{ background: "var(--od-scrim)" }}
    >
      <div
        className="border-od-border-9 bg-od-panel w-full max-w-[620px] overflow-hidden rounded-[14px] border"
        style={{ boxShadow: "0 26px 70px var(--od-scrim-3)" }}
      >
        <div className="border-od-border flex items-start justify-between gap-4 border-b p-[20px_24px_16px]">
          <div>
            <h2 className="text-od-text m-0 text-[19px] font-semibold">{t.dialog_title}</h2>
            <div className="text-od-muted-4 mt-1 text-[13px]">{t.dialog_note}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t.close}
            className="border-od-border-2 text-od-muted-4 hover:bg-od-raise hover:text-od-text size-[30px] flex-none cursor-pointer rounded-[7px] border bg-transparent text-[15px] leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-[20px_24px]">
          <label
            htmlFor="service-name"
            className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium"
          >
            {t.form_name}
          </label>
          <input
            id="service-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t.form_name_placeholder}
            className="border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-full rounded-lg border p-[10px_13px] text-[15px] outline-none"
          />

          <div className="mt-4">
            <div className="text-od-text-5 mb-[6px] text-[12.5px] font-medium">{t.form_length}</div>
            <div className="flex flex-wrap gap-[6px]">
              {LENGTH_CHOICES.map((entry) => (
                <Chip
                  key={entry}
                  label={interpolate(t.minutes, { count: entry })}
                  on={length === entry}
                  onClick={() => setLength(entry)}
                />
              ))}
              {/* Some work genuinely has no fixed length, and null is how the row
                  says so - not a zero, which would be a length of none. */}
              <Chip label={t.length_none} on={length === null} onClick={() => setLength(null)} />
            </div>
          </div>

          <div className="mt-4">
            <div className="text-od-text-5 mb-[6px] text-[12.5px] font-medium">
              {t.form_price_mode}
            </div>
            <div className="flex flex-wrap gap-[6px]">
              {PRICE_MODES.map((entry) => (
                <Chip
                  key={entry.id}
                  label={t[entry.label]}
                  on={mode === entry.id}
                  onClick={() => setMode(entry.id)}
                />
              ))}
            </div>
            {/* Hidden rather than disabled when the price is on request: an amount
                that cannot be stored must not be sitting on the screen looking as
                though it will be. */}
            {mode === "on_request" ? null : (
              <div className="mt-[10px]">
                <div className="flex items-center gap-2">
                  <input
                    value={amount}
                    onChange={(event) => setAmount(event.target.value)}
                    placeholder={t.form_price_placeholder}
                    inputMode="decimal"
                    aria-label={t.form_price}
                    dir="ltr"
                    className="mono ltr-data border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-[160px] rounded-lg border p-[10px_13px] text-[14px] outline-none"
                  />
                  <span className="text-od-muted-5 text-[13px]">{currency}</span>
                </div>
                <div className="text-od-faint mt-[6px] text-[12.5px] text-pretty">
                  {t.form_price_note}
                </div>
              </div>
            )}
          </div>

          <div className="mt-4">
            <label
              htmlFor="service-says"
              className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium"
            >
              {t.form_says}
            </label>
            <textarea
              id="service-says"
              rows={2}
              value={says}
              onChange={(event) => setSays(event.target.value)}
              placeholder={t.form_says_placeholder}
              className="border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-full resize-y rounded-lg border p-[11px_13px] text-[14.5px] leading-[1.55] outline-none"
            />
          </div>

          <div className="mt-4">
            <label
              htmlFor="service-who"
              className="text-od-text-5 mb-[6px] block text-[12.5px] font-medium"
            >
              {t.form_who}
            </label>
            {/* Free text, not a list of staff: the person who does the work often has
                no login here, and a name is what the caller is told. Empty is "any
                free", which is what most work is. */}
            <input
              id="service-who"
              value={who}
              onChange={(event) => setWho(event.target.value)}
              placeholder={t.form_who_placeholder}
              className="border-od-border-6 bg-od-panel-deep-3 text-od-text-2 w-full rounded-lg border p-[10px_13px] text-[14.5px] outline-none"
            />
          </div>

          <div className="border-od-border-4 bg-od-panel-deep-4 mt-[18px] flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-[10px] border p-[14px_16px]">
            <div className="max-w-[52ch] min-w-0">
              <div className="text-od-text-5 text-[12.5px] font-medium">{t.bookable_heading}</div>
              <div className="text-od-muted-5 mt-[3px] text-[12.5px] text-pretty">
                {bookable ? t.bookable_on : t.bookable_off}
              </div>
            </div>
            <Switch on={bookable} onClick={() => setBookable((value) => !value)} />
          </div>

          {failed ? (
            <div
              role="alert"
              className="border-od-red-border bg-od-red-bg mt-[14px] rounded-[9px] border p-[11px_15px] text-[13px] text-[color:var(--od-red-text-2)]"
            >
              {t.save_failed}
            </div>
          ) : null}
        </div>

        <div className="border-od-border bg-od-panel-deep-2 flex flex-wrap justify-end gap-[10px] border-t p-[16px_24px]">
          <button
            type="button"
            onClick={onClose}
            className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-[7px] border bg-transparent p-[9px_15px]"
          >
            {t.cancel}
          </button>
          <button
            type="button"
            disabled={!ready || saving}
            onClick={() => void save()}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 cursor-pointer rounded-[7px] border p-[9px_17px] font-semibold disabled:cursor-default disabled:opacity-50"
          >
            {saving ? t.saving : t.save}
          </button>
        </div>
      </div>
    </div>
  );
}

function ServerError({ t, onRetry }: { t: CatalogueDictionary; onRetry: () => void }) {
  return (
    <div className="flex justify-center py-20">
      <div className="border-od-border-9 bg-od-panel w-full max-w-[560px] rounded-xl border p-8">
        <h2 className="mt-0 mb-0 text-[21px] font-semibold">{t.error_title}</h2>
        <p className="text-od-muted mt-[10px] max-w-[46ch] text-pretty">{t.error_body}</p>
        <button
          type="button"
          onClick={onRetry}
          className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 mt-5 cursor-pointer rounded-md border p-[9px_16px] font-medium"
        >
          {t.error_retry}
        </button>
      </div>
    </div>
  );
}

const SHIMMER = {
  background: "linear-gradient(90deg,var(--od-raise-4),var(--od-raise-13),var(--od-raise-4))",
  backgroundSize: "420px 100%",
  animation: "od-shimmer 1.4s linear infinite",
};

function CatalogueSkeleton() {
  return (
    <div>
      <div className="h-[30px] w-[220px] rounded-md" style={SHIMMER} />
      <div className="border-od-line bg-od-panel-deep-3 mt-[22px] overflow-hidden rounded-[10px] border">
        {[64, 78, 56, 82, 70, 60].map((width, index) => (
          <div
            key={index}
            className="flex items-center gap-4 border-b border-[color:var(--od-raise-6)] p-[16px_18px]"
          >
            <div
              className="h-[14px] flex-[1_1_auto] rounded"
              style={{ maxWidth: `${width}%`, ...SHIMMER }}
            />
            <div className="h-3 w-[70px] flex-none rounded bg-[var(--od-raise-4)]" />
            <div className="h-3 w-[70px] flex-none rounded bg-[var(--od-raise-4)]" />
            <div className="h-5 w-10 flex-none rounded-full bg-[var(--od-raise-8)]" />
          </div>
        ))}
      </div>
    </div>
  );
}
