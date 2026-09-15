/**
 * Minimal data-fetching hooks (ADR-021 p.6: no react-query for the MVP — a
 * thin `useAsync` over the typed client is enough for five read-mostly
 * screens). Server state is never copied into app state; a refetch replaces
 * it in place: the first load shows the loader, reloads swap data silently.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./client";

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
}

export function useAsync<T>(fetcher: () => Promise<T>, deps: readonly unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);

  // Runs before the fetch effect below, so every fetch reads the latest
  // inline closure without making it a dependency.
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    let cancelled = false;
    fetcherRef
      .current()
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setLoading(false);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof ApiError ? cause : new ApiError(0, null, String(cause)));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // deps is an explicit dependency list handed in by the caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { data, loading, error, reload };
}
