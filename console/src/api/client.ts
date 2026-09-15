/**
 * Thin typed fetch client for the Dark Factory API (T036, ADR-021 p.4/p.6).
 *
 * Auth rules (ADR-009 p.7 local contour, ADR-021 p.4):
 * - GET requests carry no token (reads are open, exposure minimized);
 * - POST requests attach `Authorization: Bearer <operator token>` and an
 *   `Idempotency-Key` (UUID v4, generated once per logical operation).
 *
 * Errors: every failed response carries the RFC 7807-like `ErrorBody`;
 * 401 means "token required", 403 "scope/role missing", 409
 * "state_revision mismatch" (`isStateRevisionConflict`).
 */

import type { ApprovalRequest, Change, ChangeCard, ChangeTrace, Decision, Evidence, Finding, FindingSeverity, FindingStatus, GateResult, RunCard, RunSummary, RunTrace, StageResult, ErrorBody } from "./types";
import { getToken } from "./token";
import { uuidV4 } from "../lib/id";

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;
  /** RFC 7807 type; "about:blank" when the body was not a problem document. */
  readonly problemType: string;

  constructor(status: number, body: Partial<ErrorBody> | null, fallback: string) {
    const detail = body?.detail ?? fallback;
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.problemType = body?.type ?? "about:blank";
    this.title = body?.title ?? fallback;
    this.detail = detail;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isStateRevisionConflict(): boolean {
    return this.status === 409;
  }
}

export class ApiNetworkError extends ApiError {
  constructor(cause: unknown) {
    super(0, null, "The API is unreachable");
    this.name = "ApiNetworkError";
    this.cause = cause;
  }
}

export interface ClientOptions {
  /** Base URL of the API, default "/api/v1" (same origin, dev proxy / nginx). */
  baseUrl?: string;
  /** Idempotency-Key for POSTs; a UUID v4 is generated when omitted. */
  idempotencyKey?: string;
  /** Test seam: replaces global fetch. */
  fetchImpl?: typeof fetch;
  /** Test seam: token source override (defaults to localStorage-backed store). */
  tokenProvider?: () => string | null;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly tokenProvider: () => string | null;

  constructor(options: ClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "/api/v1").replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch.bind(globalThis);
    this.tokenProvider = options.tokenProvider ?? getToken;
  }

  private async request<T>(
    method: "GET" | "POST",
    path: string,
    init?: { body?: unknown; params?: Record<string, string | number | undefined>; idempotencyKey?: string },
  ): Promise<T> {
    const url = new URL(this.baseUrl + path, window.location.origin);
    for (const [key, value] of Object.entries(init?.params ?? {})) {
      if (value !== undefined) {
        url.searchParams.set(key, String(value));
      }
    }
    const headers: Record<string, string> = {};
    let body: string | undefined;
    if (init?.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }
    if (method === "POST") {
      // Writes only: the token is never attached to reads (ADR-021 p.4).
      const token = this.tokenProvider();
      if (token) {
        headers.Authorization = `Bearer ${token}`;
      }
      headers["Idempotency-Key"] = init?.idempotencyKey ?? uuidV4();
    }

    let response: Response;
    try {
      response = await this.fetchImpl(url.toString(), { method, headers, body });
    } catch (cause: unknown) {
      throw new ApiNetworkError(cause);
    }
    if (!response.ok) {
      throw await this.toApiError(response);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  private async toApiError(response: Response): Promise<ApiError> {
    const fallback = `${response.status} ${response.statusText}`.trim();
    try {
      const body = (await response.json()) as Partial<ErrorBody>;
      return new ApiError(response.status, body, fallback);
    } catch {
      // Non-JSON error body: keep the status, no invented detail.
      return new ApiError(response.status, null, fallback);
    }
  }

  // -- runs ---------------------------------------------------------------

  listRuns(params?: {
    change_id?: string;
    status?: string;
    stage?: string;
    limit?: number;
    offset?: number;
  }): Promise<RunSummary[]> {
    return this.request("GET", "/runs", { params });
  }

  getRun(runId: string): Promise<RunCard> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}`);
  }

  getRunStageResults(runId: string): Promise<StageResult[]> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}/stage-results`);
  }

  getRunTrace(runId: string): Promise<RunTrace> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}/trace`);
  }

  getRunEvidence(runId: string): Promise<Evidence[]> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}/evidence`);
  }

  getRunGates(runId: string): Promise<GateResult[]> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}/gates`);
  }

  getRunFindings(
    runId: string,
    params?: { severity?: FindingSeverity; status?: FindingStatus },
  ): Promise<Finding[]> {
    return this.request("GET", `/runs/${encodeURIComponent(runId)}/findings`, { params });
  }

  // -- changes ------------------------------------------------------------

  listChanges(params?: { limit?: number; offset?: number }): Promise<Change[]> {
    return this.request("GET", "/changes", { params });
  }

  /** Intake (FR-001). Requires a token with `changes:write`; 201 created, 200 replay. */
  createChange(change: Change, options?: { idempotencyKey?: string }): Promise<Change> {
    return this.request("POST", "/changes", {
      body: change,
      idempotencyKey: options?.idempotencyKey,
    });
  }

  getChange(changeId: string): Promise<ChangeCard> {
    return this.request("GET", `/changes/${encodeURIComponent(changeId)}`);
  }

  getChangeTrace(changeId: string): Promise<ChangeTrace> {
    return this.request("GET", `/changes/${encodeURIComponent(changeId)}/trace`);
  }

  getChangeApprovals(changeId: string): Promise<Decision[]> {
    return this.request("GET", `/changes/${encodeURIComponent(changeId)}/approvals`);
  }

  /**
   * Record one version-bound operator decision. Requires a token with
   * `approvals:write` and the operator role (agents never approve).
   * 409 = `expected_state_revision` mismatch: re-read the card and retry.
   */
  recordApproval(
    changeId: string,
    body: ApprovalRequest,
    options?: { idempotencyKey?: string },
  ): Promise<Decision> {
    return this.request("POST", `/changes/${encodeURIComponent(changeId)}/approvals`, {
      body,
      idempotencyKey: options?.idempotencyKey,
    });
  }
}

/** Shared client instance; baseUrl comes from the settings store. */
export function createApiClient(baseUrl?: string): ApiClient {
  return new ApiClient(baseUrl ? { baseUrl } : {});
}
