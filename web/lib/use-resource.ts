"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, OfflineError } from "./api";

export type ResourceError = { kind: "offline" | "forbidden" | "failed"; message: string };

/**
 * One fetch, and the three states a screen has to draw for it.
 *
 * Every wired screen needs the same four things — the data, whether it is still
 * arriving, what went wrong, and a way to ask again — so they are written once here
 * rather than four times with three different bugs.
 *
 * **`loading` is derived, never stored.** It is "the request I want is not the request
 * that settled", which is true from the first render onwards without anything having
 * to set it. Storing it would mean setting state inside the effect that starts the
 * fetch, which costs an extra render pass on every load and is what
 * `react-hooks/set-state-in-effect` exists to stop.
 *
 * **Old data survives a reload.** The result is replaced only when the new one lands,
 * so pressing "re-check" leaves the screen readable instead of blanking it and then
 * filling it back in.
 */
export type Resource<T> = {
  data: T | null;
  loading: boolean;
  /** `offline` is kept apart from a server error on purpose: "the server refused" and
   *  "the server could not be reached" need different words and different advice. */
  error: ResourceError | null;
  reload: () => void;
};

function describe(thrown: unknown): ResourceError {
  if (thrown instanceof OfflineError) return { kind: "offline", message: thrown.message };
  if (thrown instanceof ApiError) {
    return { kind: thrown.status === 403 ? "forbidden" : "failed", message: thrown.message };
  }
  return { kind: "failed", message: String(thrown) };
}

export function useResource<T>(load: () => Promise<T>, deps: unknown[] = []): Resource<T> {
  const [nonce, setNonce] = useState(0);
  // The identity of the request currently wanted. Anything that should cause a refetch
  // goes in here, and nothing else does.
  const key = JSON.stringify([nonce, ...deps]);

  const [result, setResult] = useState<{ key: string; data: T | null; error: ResourceError | null }>(
    { key: "", data: null, error: null },
  );

  // Held in a ref so a caller passing a fresh arrow function on every render — which
  // every caller does — is not itself a reason to refetch. Assigned in an effect
  // rather than during render, because a ref written during render is not a stable
  // value for anything that reads it in the same pass.
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  useEffect(() => {
    let alive = true;
    loadRef
      .current()
      .then((data) => {
        // `alive` is the whole guard against an overtaken request: when `key` changes,
        // React runs this cleanup before the next effect, so a slow earlier reply can
        // never overwrite a newer one.
        if (alive) setResult({ key, data, error: null });
      })
      .catch((thrown: unknown) => {
        if (alive) setResult({ key, data: null, error: describe(thrown) });
      });
    return () => {
      alive = false;
    };
  }, [key]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  return {
    data: result.data,
    loading: result.key !== key,
    error: result.key === key ? result.error : null,
    reload,
  };
}
