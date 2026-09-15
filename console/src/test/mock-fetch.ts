/**
 * Global fetch stub for component tests: routes requests by method + pathname
 * regex. Responses are RFC 7807-like JSON on failures, mirroring the T035
 * contract. No secrets — synthetic data only.
 */

import { vi } from "vitest";

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export interface FetchRoute {
  method: string;
  pattern: RegExp;
  handler: (url: URL, init: RequestInit) => Response | Promise<Response>;
}

export type FetchMock = ReturnType<typeof vi.fn>;

/** Installs a global fetch stub matching routes in order; 404 on miss. */
export function stubFetch(routes: FetchRoute[]): FetchMock {
  const fetchMock = vi.fn(
    async (input: string | URL | Request, init: RequestInit = {}): Promise<Response> => {
      const rawUrl =
        typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(rawUrl);
      const method = (init.method ?? "GET").toUpperCase();
      for (const route of routes) {
        if (route.method === method && route.pattern.test(url.pathname)) {
          return route.handler(url, init);
        }
      }
      return jsonResponse(404, {
        type: "about:blank",
        title: "Not Found",
        status: 404,
        detail: `unmatched ${method} ${url.pathname}`,
      });
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Decodes the JSON body of a captured fetch call. */
export function bodyOf(init: RequestInit): Record<string, unknown> {
  return JSON.parse(init.body as string) as Record<string, unknown>;
}
