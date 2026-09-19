/**
 * Thin typed fetch client for the Dark Factory API (T036, ADR-021 p.4/p.6).
 *
 * Auth rules (ADR-009 p.7 local contour, ADR-021 p.4):
 * - GET requests carry no token (reads are open, exposure minimized);
 * - writes (POST/PUT) attach `Authorization: Bearer <operator token>` and an
 *   `Idempotency-Key` (UUID v4, generated once per logical operation).
 *
 * Errors: every failed response carries the RFC 7807-like `ErrorBody`;
 * 401 means "token required", 403 "scope/role missing", 409
 * "state_revision mismatch" (`isStateRevisionConflict`).
 */

import type {
  ApprovalRequest,
  BriefFormulateRequest,
  Change,
  ChangeCard,
  ChangeTrace,
  CiStage,
  CiStages,
  Decision,
  ErrorBody,
  Evidence,
  Finding,
  FindingSeverity,
  FindingStatus,
  GateResult,
  Guidance,
  IntakeBrief,
  Product,
  ProductCreateRequest,
  ProductValidationView,
  RunCard,
  RunSummary,
  RunTrace,
  StageResult,
} from "./types";
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
  /** Idempotency-Key for writes; a UUID v4 is generated when omitted. */
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
    method: "GET" | "POST" | "PUT",
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
    if (method !== "GET") {
      // Writes only (POST/PUT): the token is never attached to reads (ADR-021 p.4).
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

  /** `product_id` narrows the list to one product's changes (T071). */
  listChanges(params?: { product_id?: string; limit?: number; offset?: number }): Promise<Change[]> {
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

  /** The operator's next step for a change (T074, ADR-033) — rendered, never computed here. */
  getChangeGuidance(changeId: string): Promise<Guidance> {
    return this.request("GET", `/changes/${encodeURIComponent(changeId)}/guidance`);
  }

  /**
   * Ask the agent to formulate a brief from free text (T072). Requires
   * `changes:write`. A harness failure is still a 200: the brief comes back
   * as a `draft` with the original `source_text` and a human-readable `error`.
   */
  formulateBrief(body: BriefFormulateRequest, options?: { idempotencyKey?: string }): Promise<IntakeBrief> {
    return this.request("POST", "/briefs/formulate", { body, idempotencyKey: options?.idempotencyKey });
  }

  /** Replace the brief of a change (T071). Requires `changes:write`; the updated change is returned. */
  updateChangeBrief(changeId: string, brief: IntakeBrief, options?: { idempotencyKey?: string }): Promise<Change> {
    return this.request("PUT", `/changes/${encodeURIComponent(changeId)}/brief`, {
      body: brief,
      idempotencyKey: options?.idempotencyKey,
    });
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

  // -- products (T065/T066, ADR-030) --------------------------------------

  listProducts(params?: { limit?: number; offset?: number }): Promise<Product[]> {
    return this.request("GET", "/products", { params });
  }

  getProduct(productId: string): Promise<Product> {
    return this.request("GET", `/products/${encodeURIComponent(productId)}`);
  }

  /** The operator's next step for a product (T074, ADR-033). */
  getProductGuidance(productId: string): Promise<Guidance> {
    return this.request("GET", `/products/${encodeURIComponent(productId)}/guidance`);
  }

  /** Register a product. Requires `products:write` + operator role; 201 created, 200 replay of the same id. */
  createProduct(body: ProductCreateRequest, options?: { idempotencyKey?: string }): Promise<Product> {
    return this.request("POST", "/products", { body, idempotencyKey: options?.idempotencyKey });
  }

  /**
   * Observe the product repository (ADR-031 p.5: mutates nothing in the
   * repository). Requires the operator token; 503 when provisioning is not
   * configured on the contour — the `detail` is shown to the operator as is.
   */
  validateProduct(productId: string, options?: { idempotencyKey?: string }): Promise<ProductValidationView> {
    return this.request("POST", `/products/${encodeURIComponent(productId)}/validate`, {
      body: {},
      idempotencyKey: options?.idempotencyKey,
    });
  }

  // -- ci stages (T058/ADR-026) -------------------------------------------

  /** The current on/off state of every factory CI stage. */
  listCiStages(): Promise<CiStages> {
    return this.request("GET", "/ci/stages");
  }

  /**
   * Switch one stage on/off. The body is a target state, not an event — the
   * call is idempotent, so no retry logic is needed. Requires a token with
   * `ci:write` and the operator role; the updated `CiStage` is returned.
   */
  setCiStageEnabled(job: string, enabled: boolean): Promise<CiStage> {
    return this.request("PUT", `/ci/stages/${encodeURIComponent(job)}`, { body: { enabled } });
  }
}

/** Shared client instance; baseUrl comes from the settings store. */
export function createApiClient(baseUrl?: string): ApiClient {
  return new ApiClient(baseUrl ? { baseUrl } : {});
}
