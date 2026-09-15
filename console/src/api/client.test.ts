import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError } from "./client";
import * as token from "./token";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function textResponse(status: number, body: string): Response {
  return new Response(body, { status });
}

const CHANGES = [{ id: "chg_1", title: "Demo" }];

describe("ApiClient GET", () => {
  it("parses JSON and builds the URL with query params", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(200, CHANGES));
    const client = new ApiClient({ baseUrl: "/api/v1", fetchImpl });
    await expect(client.listChanges({ limit: 50 })).resolves.toEqual(CHANGES);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://localhost:3000/api/v1/changes?limit=50");
    expect(init.method).toBe("GET");
  });

  it("does not attach Authorization or Idempotency-Key to reads", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("secret-token");
    const fetchImpl = vi.fn(async () => jsonResponse(200, []));
    const client = new ApiClient({ fetchImpl });
    await client.listChanges();
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
    expect(headers["Idempotency-Key"]).toBeUndefined();
    vi.restoreAllMocks();
  });

  it("throws ApiError with the RFC 7807 body on 404", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(404, { type: "about:blank", title: "Not Found", status: 404, detail: "Run 'x' does not exist" }),
    );
    const client = new ApiClient({ fetchImpl });
    const error = await client.getRun("x").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(404);
    expect(apiError.title).toBe("Not Found");
    expect(apiError.detail).toBe("Run 'x' does not exist");
  });
});

describe("ApiClient auth semantics", () => {
  beforeEach(() => {
    vi.spyOn(token, "getToken").mockReturnValue("secret-token");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("attaches Bearer and Idempotency-Key (UUID v4) to POST /changes", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(201, CHANGES[0]));
    const client = new ApiClient({ fetchImpl });
    const change = { ...CHANGES[0] };
    await client.createChange(change as never);
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer secret-token");
    expect(headers["Idempotency-Key"]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(change);
  });

  it("reuses an explicitly provided Idempotency-Key", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse(201, {}));
    const client = new ApiClient({ fetchImpl });
    await client.recordApproval("chg_1", { gate: "review", outcome: "approved", subject_revision: "abc" }, {
      idempotencyKey: "fixed-key",
    });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/changes/chg_1/approvals");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("fixed-key");
  });

  it("maps 401 to ApiError.isUnauthorized", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(401, { type: "about:blank", title: "Unauthorized", status: 401, detail: "A valid bearer token is required" }),
    );
    const client = new ApiClient({ fetchImpl });
    const error = (await client.createChange({} as never).catch((caught: unknown) => caught)) as ApiError;
    expect(error.isUnauthorized).toBe(true);
    expect(error.status).toBe(401);
  });

  it("maps 403 scope error to ApiError.isForbidden", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(403, { type: "about:blank", title: "Forbidden", status: 403, detail: "Scope 'approvals:write' is required" }),
    );
    const client = new ApiClient({ fetchImpl });
    const error = (await client
      .recordApproval("chg_1", { gate: "review", outcome: "approved", subject_revision: "abc" })
      .catch((caught: unknown) => caught)) as ApiError;
    expect(error.isForbidden).toBe(true);
    expect(error.detail).toContain("approvals:write");
  });

  it("maps 409 to isStateRevisionConflict", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(409, { type: "about:blank", title: "Conflict", status: 409, detail: "state_revision mismatch" }),
    );
    const client = new ApiClient({ fetchImpl });
    const error = (await client
      .recordApproval("chg_1", { gate: "review", outcome: "approved", subject_revision: "abc" })
      .catch((caught: unknown) => caught)) as ApiError;
    expect(error.isStateRevisionConflict).toBe(true);
    expect(error.detail).toBe("state_revision mismatch");
  });

  it("survives a non-JSON error body", async () => {
    const fetchImpl = vi.fn(async () => textResponse(500, "boom"));
    const client = new ApiClient({ fetchImpl });
    const error = (await client.listChanges().catch((caught: unknown) => caught)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(500);
    expect(error.problemType).toBe("about:blank");
    // undici Response has no statusText; the fallback is the bare status code.
    expect(error.detail).toBe("500");
  });

  it("wraps network failures into ApiNetworkError", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("failed to fetch");
    });
    const client = new ApiClient({ fetchImpl });
    const error = (await client.listChanges().catch((caught: unknown) => caught)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.name).toBe("ApiNetworkError");
  });
});
