import { useEffect, useState } from "react";
import { ApiError, getJson, type Timing } from "./api";

/** Waits before retrying a "server is busy" (503) answer; an import in progress is reported, not retried. */
const BUSY_RETRY_MS = [1500, 4000, 8000];

export interface ApiState<T> {
  /** Last good response. Kept while a newer request is in flight so the page does not blank out. */
  data: T | null;
  error: string | null;
  /** True while the request for the CURRENT parameters has not completed. */
  loading: boolean;
  timing: Timing | null;
  reload: () => void;
}

interface Slot<T> { key: string | null; nonce: number; data: T | null; error: string | null; timing: Timing | null }

/**
 * GET `path` with `params`; pass `path = null` to stay idle. Changing the parameters aborts the request in
 * flight (the server sees the disconnect) and starts a new one; unmounting aborts too.
 */
export function useApi<T>(path: string | null, params: URLSearchParams | null): ApiState<T> {
  const key = path === null ? null : `${path}?${params?.toString() ?? ""}`;
  const [slot, setSlot] = useState<Slot<T>>({ key: null, nonce: -1, data: null, error: null, timing: null });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (key === null || path === null) return;
    const ac = new AbortController();
    let timer = 0;
    const attempt = (n: number) => {
      getJson<T>(path, params, ac.signal)
        .then((r) => setSlot({ key, nonce, data: r.data, error: null, timing: r.timing }))
        .catch((e: unknown) => {
          if (ac.signal.aborted) return;
          const busy = e instanceof ApiError && e.status === 503 && !/import/i.test(e.message);
          const wait = BUSY_RETRY_MS[n];
          if (busy && wait !== undefined) { timer = window.setTimeout(() => attempt(n + 1), wait); return; }
          setSlot((s) => ({ ...s, key, nonce, error: e instanceof Error ? e.message : String(e) }));
        });
    };
    attempt(0);
    return () => { ac.abort(); window.clearTimeout(timer); };
    // `params` is represented by `key`; re-running on object identity would refetch every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, nonce]);

  return {
    data: slot.data, error: slot.key === key && slot.nonce === nonce ? slot.error : null,
    loading: key !== null && (slot.key !== key || slot.nonce !== nonce), timing: slot.timing,
    reload: () => setNonce((n) => n + 1),
  };
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}
