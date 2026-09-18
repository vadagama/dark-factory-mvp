/**
 * Minimal data-fetching hooks (ADR-021 p.6 still holds: no react-query for the
 * MVP — a thin `useAsync` over the typed client is enough for the read-mostly
 * screens). Server state is never copied into app state; a refetch replaces
 * it in place: the first load shows the loader, reloads swap data silently.
 *
 * Polling (T-095): pass `options.pollMs` and a read-mostly screen refreshes
 * itself while the tab is visible. The loop is a chain of setTimeouts, never
 * setInterval: the next request is armed only after the previous one settles,
 * so slow responses cannot overlap and at most one timer is pending. A
 * background refresh is silent: `loading` stays false, data is replaced in
 * place, `updatedAt` stamps the last success. A failed background refresh
 * keeps the last good data and leaves `error` untouched — `error` is only set
 * when a fetch fails while there is nothing to show (initial load or fresh
 * deps) — and the chain is re-armed, so a transient blip delays the loop by
 * one interval instead of killing it. While the tab is hidden nothing runs
 * and nothing is scheduled; on return the screen refreshes immediately and
 * the chain resumes. `reload()` stays an immediate manual refresh and never
 * doubles the chain: arming always clears the pending timer first.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./client";

/** Background refresh interval for the polling screens (T-095). */
export const POLL_MS = 10_000;

export interface UseAsyncOptions {
  /** Poll interval in ms while the tab is visible; falsy disables polling. */
  pollMs?: number;
}

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  /** Time of the last successful fetch (Date.now()); null before the first one. */
  updatedAt: number | null;
  reload: () => void;
}

export function useAsync<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
  options?: UseAsyncOptions,
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);
  const pollMs = options?.pollMs;

  // Runs before the fetch effect below, so every fetch reads the latest
  // inline closure without making it a dependency.
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // True once any fetch of this cycle resolved: a later failure is a failed
    // refresh of data already on screen, not a lost initial load.
    let hasData = false;

    const clearTimer = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    // The polling chain: arm at most one timer, only while the tab is visible.
    const armPolling = () => {
      if (!pollMs || cancelled || document.visibilityState === "hidden") {
        return;
      }
      clearTimer();
      timer = setTimeout(() => {
        timer = null;
        if (!cancelled && document.visibilityState !== "hidden") {
          void refresh(true);
        }
      }, pollMs);
    };

    const refresh = (background: boolean) => {
      fetcherRef
        .current()
        .then((result) => {
          if (cancelled) {
            return;
          }
          hasData = true;
          setData(result);
          setError(null);
          setLoading(false);
          setUpdatedAt(Date.now());
          armPolling();
        })
        .catch((cause: unknown) => {
          if (cancelled) {
            return;
          }
          if (hasData) {
            // Refresh failed with data on screen: keep the last good data and
            // the current error so a transient blip cannot blank the screen;
            // a background failure re-arms the chain for the next attempt.
            if (background) {
              armPolling();
            }
            return;
          }
          // Nothing to show (initial load / fresh deps): surface the failure.
          // No data to keep fresh — polling stays off until the next reload().
          setError(cause instanceof ApiError ? cause : new ApiError(0, null, String(cause)));
          setLoading(false);
        });
    };

    refresh(false);

    const onVisibilityChange = () => {
      if (cancelled) {
        return;
      }
      if (document.visibilityState === "hidden") {
        clearTimer();
      } else if (pollMs) {
        // Back to a visible tab: refresh now; a successful refresh re-arms
        // the chain (armPolling skips scheduling while hidden).
        clearTimer();
        void refresh(true);
      }
    };

    if (pollMs) {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    return () => {
      cancelled = true;
      clearTimer();
      if (pollMs) {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
    };
    // deps is an explicit dependency list handed in by the caller; pollMs is
    // read once per cycle (the pages pass a constant).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { data, loading, error, updatedAt, reload };
}
