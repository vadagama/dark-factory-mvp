/**
 * Thin typed fetch client for the example product API.
 *
 * Same-origin contract (ADR-021 p.3 pattern): the browser always calls
 * relative `/api/...` paths; the dev proxy (vite) or nginx routes them to
 * the backend. No CORS, no absolute URLs, no tokens in this blueprint.
 */

export interface Health {
  status: string;
  database: string;
}

export class ApiError extends Error {
  /** HTTP status; 0 means the network itself failed (backend unreachable). */
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = `API error ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      detail = body.detail;
    }
  } catch {
    // Non-JSON error body: keep the generic message.
  }
  return new ApiError(response.status, detail);
}

/** GET /api/healthz — readiness probe of the backend (database ping). */
export async function fetchHealth(fetchImpl: typeof fetch = fetch): Promise<Health> {
  let response: Response;
  try {
    response = await fetchImpl("/api/healthz");
  } catch {
    throw new ApiError(0, "The API is unreachable");
  }
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as Health;
}
