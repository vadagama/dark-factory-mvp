import { describe, expect, it, vi } from "vitest";

import { ApiError, fetchHealth } from "./client";

const okResponse = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

describe("fetchHealth", () => {
  it("parses a 200 health payload", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(okResponse({ status: "ok", database: "ok" }));

    const health = await fetchHealth(fetchImpl);

    expect(fetchImpl).toHaveBeenCalledWith("/api/healthz");
    expect(health).toEqual({ status: "ok", database: "ok" });
  });

  it("raises ApiError on a non-2xx response", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "database unavailable" }), {
        status: 503,
        headers: { "content-type": "application/json" },
      }),
    );

    const error = await fetchHealth(fetchImpl).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).message).toBe("database unavailable");
  });

  it("raises ApiError with status 0 when the network fails", async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("fetch failed"));

    const error = await fetchHealth(fetchImpl).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
  });
});
