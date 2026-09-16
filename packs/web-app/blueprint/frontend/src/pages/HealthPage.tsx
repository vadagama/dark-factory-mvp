import { useCallback, useEffect, useState } from "react";

import { ApiError, fetchHealth, type Health } from "../api/client";
import { Button } from "@small/ui";

type PageState =
  | { phase: "loading" }
  | { phase: "ready"; health: Health }
  | { phase: "error"; message: string };

/**
 * First page of the blueprint: calls the backend /api/healthz and shows
 * liveness + database status. Products replace this page; the point is to
 * demonstrate the client, the @small/ui stub and state handling.
 */
export function HealthPage() {
  const [state, setState] = useState<PageState>({ phase: "loading" });

  // Pure fetcher: it returns the next page state instead of setting it, so no
  // setState is reachable synchronously from the effect below (eslint-plugin
  // react-hooks set-state-in-effect). Reloads reset the phase in the handlers.
  const fetchPage = useCallback(async (): Promise<PageState> => {
    try {
      const health = await fetchHealth();
      return { phase: "ready", health };
    } catch (error) {
      const message =
        error instanceof ApiError
          ? `Backend unavailable: ${error.message} (status ${error.status})`
          : `Backend unavailable: ${String(error)}`;
      return { phase: "error", message };
    }
  }, []);

  useEffect(() => {
    let alive = true;
    void fetchPage().then((next) => {
      if (alive) setState(next);
    });
    return () => {
      alive = false;
    };
  }, [fetchPage]);

  const reload = useCallback(() => {
    setState({ phase: "loading" });
    void fetchPage().then(setState);
  }, [fetchPage]);

  if (state.phase === "loading") {
    return <p>Loading…</p>;
  }

  if (state.phase === "error") {
    return (
      <main>
        <h1>example-product</h1>
        <p role="alert">{state.message}</p>
        <Button onClick={reload}>Retry</Button>
      </main>
    );
  }

  return (
    <main>
      <h1>example-product</h1>
      <p>
        Backend: <strong>{state.health.status}</strong>
      </p>
      <p>
        Database: <strong>{state.health.database}</strong>
      </p>
      <Button onClick={reload}>Refresh</Button>
    </main>
  );
}
