"use client";

import { useState } from "react";

import { Sidebar } from "@/components/shell/sidebar";
import {
  addNumber,
  ApiError,
  numbersList,
  releaseNumber,
  setNumberStatus,
  type PhoneNumber,
} from "@/lib/api";
import { interpolate } from "@/lib/i18n";
import type { Locale } from "@/lib/locales";
import { useResource } from "@/lib/use-resource";

import type { NumbersDictionary } from "./page";

const COLUMNS = "minmax(0,1.3fr) minmax(0,1fr) minmax(112px,max-content) minmax(0,1fr) max-content";

export function Numbers({ locale, t }: { locale: Locale; t: NumbersDictionary }) {
  const list = useResource<PhoneNumber[]>(() => numbersList(), []);

  const [adding, setAdding] = useState(false);
  const [e164, setE164] = useState("");
  const [provider, setProvider] = useState("");
  const [busy, setBusy] = useState<number | "add" | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const rows = list.data ?? [];

  /** The screen's own words for a code it knows; the server's prose for one it
   *  does not. Screens branch on `code`, never on `message`. */
  const describe = (thrown: unknown): string => {
    if (thrown instanceof ApiError) {
      if (thrown.code === "invalid_e164") return t.error_invalid_e164;
      if (thrown.code === "number_taken") return t.error_number_taken;
      if (thrown.code === "platform_number") return t.error_platform_number;
      return thrown.message;
    }
    return String(thrown);
  };

  const submit = async () => {
    setBusy("add");
    setNotice(null);
    try {
      await addNumber(e164.trim(), provider.trim());
      setE164("");
      setProvider("");
      setAdding(false);
      list.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(null);
    }
  };

  const toggle = async (row: PhoneNumber) => {
    setBusy(row.id);
    setNotice(null);
    try {
      await setNumberStatus(row.id, row.status === "active" ? "disabled" : "active");
      list.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(null);
    }
  };

  const release = async (row: PhoneNumber) => {
    setBusy(row.id);
    setNotice(null);
    try {
      await releaseNumber(row.id);
      setConfirming(null);
      list.reload();
    } catch (thrown) {
      setNotice(describe(thrown));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="bg-od-canvas text-od-text-2 min-h-dvh text-[14px] leading-[1.45] ps-[224px]">
      <div className="fixed inset-y-0 start-0 z-50 h-dvh w-[224px]">
        <Sidebar locale={locale} active="numbers" />
      </div>

      <div className="mx-auto max-w-[1400px] p-[22px_28px_60px]">
        <div className="flex flex-wrap items-end justify-between gap-x-5 gap-y-[14px]">
          <div className="max-w-[64ch]">
            <h1 className="text-od-text m-0 text-[26px] font-semibold tracking-[-0.02em]">
              {t.title}
            </h1>
            <p className="text-od-muted-4 mt-[6px] text-pretty">
              {rows.length === 1
                ? t.count_one
                : interpolate(t.count_many, { total: rows.length })}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setAdding((value) => !value)}
            className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 flex-none cursor-pointer rounded-[7px] border p-[9px_15px] font-medium"
          >
            {t.add_number}
          </button>
        </div>

        {adding ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
            className="border-od-line bg-od-panel-deep-3 mt-[18px] rounded-[10px] border p-[16px_18px]"
          >
            <div className="flex flex-wrap items-end gap-[14px]">
              <label className="flex min-w-[220px] flex-[1_1_240px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_number}
                </span>
                <input
                  value={e164}
                  onChange={(event) => setE164(event.target.value)}
                  placeholder="+43 720 123456"
                  dir="ltr"
                  required
                  maxLength={25}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 mono ltr-data rounded-lg border p-[9px_13px] text-start outline-none"
                />
              </label>
              <label className="flex min-w-[180px] flex-[1_1_200px] flex-col gap-[6px]">
                <span className="text-od-faint text-[11px] font-semibold tracking-[.08em] uppercase">
                  {t.field_provider}
                </span>
                <input
                  value={provider}
                  onChange={(event) => setProvider(event.target.value)}
                  placeholder={t.field_provider_hint}
                  required
                  maxLength={64}
                  className="border-od-border-6 bg-od-canvas-2 text-od-text-2 rounded-lg border p-[9px_13px] outline-none"
                />
              </label>
              <button
                type="submit"
                disabled={busy === "add"}
                className="border-od-stroke bg-od-raise-10 text-od-text-2 hover:bg-od-border-3 cursor-pointer rounded-lg border p-[9px_15px] font-medium disabled:cursor-default disabled:opacity-50"
              >
                {t.add_confirm}
              </button>
              <button
                type="button"
                onClick={() => {
                  setAdding(false);
                  setNotice(null);
                }}
                className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-lg border bg-transparent p-[9px_15px]"
              >
                {t.add_cancel}
              </button>
            </div>
            {/* The provider contract stays the customer's own; SIP credentials arrive
                with the SIP milestone, encrypted, and are not asked for before it. */}
            <p className="text-od-faint m-0 mt-[10px] max-w-[70ch] text-[12.5px] text-pretty">
              {t.add_note}
            </p>
          </form>
        ) : null}

        {notice ? (
          <div className="border-od-red-border-3 bg-od-red-bg-4 mt-[14px] rounded-[9px] border p-[10px_14px] text-[13px] text-[color:var(--od-red-text-6)]">
            {notice}
          </div>
        ) : null}

        {list.loading && list.data === null ? <NumbersSkeleton /> : null}

        {list.error !== null && list.data === null ? (
          <div className="border-od-red-border-3 bg-od-red-bg-4 mt-5 rounded-[10px] border p-[18px_20px]">
            <h3 className="m-0 text-[16px] font-semibold text-[color:var(--od-red-text-3)]">
              {list.error.kind === "offline" ? t.error_offline_title : t.error_failed_title}
            </h3>
            <p className="mt-[6px] max-w-[62ch] text-[13px] text-pretty text-[color:var(--od-red-text-6)]">
              {list.error.message}
            </p>
            <button
              type="button"
              onClick={list.reload}
              className="border-od-stroke bg-od-raise-10 text-od-text-2 mt-[14px] cursor-pointer rounded-[7px] border p-[8px_14px] text-[13px]"
            >
              {t.retry}
            </button>
          </div>
        ) : null}

        {list.data !== null && rows.length === 0 ? (
          <div className="border-od-border-6 bg-od-panel-deep-2 mt-[18px] rounded-[10px] border border-dashed p-[40px_28px]">
            <h3 className="m-0 text-[18px] font-semibold">{t.empty_title}</h3>
            <p className="text-od-muted mt-[10px] max-w-[62ch] text-pretty">{t.empty_body}</p>
          </div>
        ) : null}

        {rows.length > 0 ? (
          <div className="border-od-line bg-od-panel-deep-3 mt-[18px] overflow-x-auto overflow-y-hidden rounded-[10px] border">
            <div
              className="border-od-line bg-od-canvas-2 text-od-faint grid gap-[18px] border-b p-[11px_18px] text-[11px] tracking-[.08em] uppercase"
              style={{ gridTemplateColumns: COLUMNS }}
            >
              <span>{t.column_number}</span>
              <span>{t.column_provider}</span>
              <span>{t.column_status}</span>
              <span>{t.column_added}</span>
              <span aria-hidden="true" />
            </div>

            {rows.map((row) => {
              const active = row.status === "active";
              const rowBusy = busy === row.id;
              return (
                <div
                  key={row.id}
                  className="grid items-center gap-[18px] border-b border-[color:var(--od-raise-6)] p-[13px_18px]"
                  style={{ gridTemplateColumns: COLUMNS }}
                >
                  <div dir="ltr" className="mono ltr-data text-od-text text-start font-medium">
                    {row.e164}
                  </div>
                  <div className="text-od-muted-2 min-w-0 truncate">{row.provider}</div>
                  <div>
                    <span
                      className="inline-flex items-center gap-[7px] rounded-md border p-[3px_10px] text-[12.5px] font-medium whitespace-nowrap"
                      style={
                        active
                          ? {
                              borderColor: "var(--od-green-border)",
                              background: "rgba(63,185,132,.11)",
                              color: "var(--od-green-text)",
                            }
                          : {
                              borderColor: "var(--od-border-9)",
                              background: "var(--od-raise-5)",
                              color: "var(--od-muted-2)",
                            }
                      }
                    >
                      {active ? t.status_active : t.status_disabled}
                    </span>
                  </div>
                  <div className="text-od-muted-5 text-[13px]">
                    {new Date(row.created_at).toLocaleDateString(locale, {
                      day: "numeric",
                      month: "long",
                      year: "numeric",
                    })}
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    <button
                      type="button"
                      disabled={rowBusy}
                      onClick={() => void toggle(row)}
                      className="border-od-border-7 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] hover:bg-[var(--od-raise-4)] disabled:cursor-default disabled:opacity-50"
                    >
                      {active ? t.action_disable : t.action_enable}
                    </button>
                    {/* Releasing is destructive, so it asks twice - in place, where
                        the second click means the same row the first one did. */}
                    {confirming === row.id ? (
                      <>
                        <button
                          type="button"
                          disabled={rowBusy}
                          onClick={() => void release(row)}
                          className="border-od-red-border-2 bg-od-red-bg-2 hover:bg-od-red-bg-3 cursor-pointer rounded-md border p-[6px_12px] text-[12.5px] font-medium text-[color:var(--od-red-text-3)] disabled:cursor-default disabled:opacity-50"
                        >
                          {t.release_confirm}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirming(null)}
                          className="border-od-border-2 text-od-muted hover:text-od-text-2 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px]"
                        >
                          {t.release_cancel}
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        disabled={rowBusy}
                        onClick={() => setConfirming(row.id)}
                        className="border-od-border-7 cursor-pointer rounded-md border bg-transparent p-[6px_12px] text-[12.5px] text-[color:var(--od-red-text-4)] hover:bg-[var(--od-raise-4)] disabled:cursor-default disabled:opacity-50"
                      >
                        {t.action_release}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function NumbersSkeleton() {
  return (
    <div className="mt-[18px] flex flex-col gap-3">
      {[0, 1, 2, 3].map((index) => (
        <div
          key={index}
          className="border-od-raise-12 h-14 rounded-[10px] border"
          style={{
            background:
              "linear-gradient(90deg,var(--od-panel),var(--od-raise-7),var(--od-panel))",
            backgroundSize: "420px 100%",
            animation: "od-shimmer 1.4s linear infinite",
          }}
        />
      ))}
    </div>
  );
}
