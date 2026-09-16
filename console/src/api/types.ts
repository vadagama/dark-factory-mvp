/**
 * Wire types of the Dark Factory API (T035) — a TypeScript mirror of the
 * domain pydantic models in `src/dark_factory/changes/` and the API DTOs in
 * `src/dark_factory/api/dto.py`. The canonical contract is
 * `docs/descriptions/api.md` (the Python models are the source of truth for
 * field names).
 *
 * Wire format rules (ADR-015): responses are the domain models without any
 * wrapper; datetimes are ISO 8601 strings; `Decimal` values (cost) are
 * strings to preserve precision; enums are stable wire strings
 * (`changes/enums.py`).
 */

// ---------------------------------------------------------------------------
// Enums (src/dark_factory/changes/enums.py — stable wire strings)
// ---------------------------------------------------------------------------

export type Provider = "gitlab" | "github";
export type Route = "quick" | "standard" | "architecture" | "foundation";
export type RiskClass = "R0" | "R1" | "R2" | "R3" | "R4";
export type BoundaryArea = "public_api" | "data_schema" | "iam" | "architecture_boundary";
export type Role =
  | "product"
  | "design"
  | "architect"
  | "infrastructure"
  | "security"
  | "develop"
  | "quality"
  | "ci_cd"
  | "operation";
export type Stage =
  | "specification"
  | "planning"
  | "construction"
  | "review_verification"
  | "release";
export type Gate =
  | "specification"
  | "planning"
  | "code"
  | "ui"
  | "review"
  | "verification"
  | "release";
export type RunStatus =
  | "pending"
  | "running"
  | "waiting"
  | "blocked"
  | "succeeded"
  | "failed"
  | "canceled"
  | "superseded";
export type StageStatus =
  | "pending"
  | "in_progress"
  | "waiting"
  | "succeeded"
  | "failed"
  | "blocked"
  | "skipped"
  | "superseded"
  | "canceled";
export type FindingSeverity = "blocker" | "major" | "minor" | "info";
export type FindingStatus = "open" | "resolved" | "waived" | "obsolete";
export type FindingOrigin = "agent" | "human" | "ci";
export type GateStatus = "pending" | "passed" | "failed" | "skipped";
export type DecisionOutcome = "approved" | "rejected" | "waived";
export type DecisionSource = "human" | "policy" | "agent";
export type ChangeSource = "tracker" | "console" | "cli" | "api";
export type ChangeRequestStatus = "draft" | "open" | "merged" | "closed";
export type EvidenceType =
  | "log"
  | "report"
  | "screenshot"
  | "sbom"
  | "spec"
  | "diff"
  | "test_results"
  | "deployment"
  | "smoke"
  | "other";

// ---------------------------------------------------------------------------
// Domain models (changes/run.py, changes/findings.py, changes/refs.py)
// ---------------------------------------------------------------------------

export interface RepositoryRef {
  provider: Provider;
  slug: string;
}

export interface ChangeRequestRef {
  repository: RepositoryRef;
  number: number;
  url: string | null;
  status: ChangeRequestStatus;
}

export interface ArtifactRef {
  artifact_type: string;
  uri: string;
  revision: string | null;
  sha256: string | null;
  producer: string | null;
}

export interface Evidence {
  id: string;
  type: EvidenceType;
  uri: string;
  checksum: string | null;
  produced_at: string | null;
  required: boolean;
  available: boolean;
}

export interface Finding {
  id: string;
  origin: FindingOrigin;
  role: Role | null;
  severity: FindingSeverity;
  category: string | null;
  file: string | null;
  line: number | null;
  reviewed_sha: string | null;
  required_action: string | null;
  status: FindingStatus;
  confidence: number | null;
  evidence_ids: string[];
}

export interface GateResult {
  gate: Gate;
  status: GateStatus;
  sha: string | null;
  summary: string | null;
  evidence_ids: string[];
}

export interface Decision {
  id: string;
  gate: Gate;
  outcome: DecisionOutcome;
  decided_by: DecisionSource;
  role: Role | null;
  decided_at: string;
  commit_sha: string | null;
  comment: string | null;
  evidence_ids: string[];
}

export interface Change {
  id: string;
  title: string;
  description: string | null;
  source: ChangeSource;
  external_ref: string | null;
  product: RepositoryRef;
  risk_class: RiskClass;
  change_request: ChangeRequestRef | null;
  created_at: string;
}

/** Immutable result of one stage attempt (GET /runs/{id}/stage-results). */
export interface StageResult {
  schema_version: 1;
  stage: Stage;
  run_id: string;
  change_id: string;
  attempt_number: number;
  input_revision: string | null;
  status: StageStatus;
  next_action: NextAction;
  artifacts: ArtifactRef[];
  evidence: Evidence[];
  gate_results: GateResult[];
  findings: Finding[];
  escalations: EscalationViolation[];
  usage: Usage | null;
  produced_at: string;
}

/** Closed discriminated union returned by every stage (changes/next_action.py). */
export type NextAction =
  | { type: "execute_stage"; next_stage: Stage; reason?: string | null }
  | { type: "wait_for_input"; reason: string }
  | { type: "wait_for_ci"; reason: string; change_request?: ChangeRequestRef | null }
  | { type: "rework"; round: number; max_rounds: number; reason: string }
  | { type: "request_approval"; gate: Gate; requested_from?: Role | null; reason?: string | null }
  | { type: "merge"; change_request: ChangeRequestRef; reason?: string | null }
  | { type: "release"; target_environment?: string; reason?: string | null }
  | { type: "stop"; outcome: "blocked" | "failed" | "canceled"; reason: string };

export interface EscalationViolation {
  rule: string;
  reason: string;
  manual_assessment: boolean;
}

export interface Usage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number | null;
  cost: string | null;
}

// ---------------------------------------------------------------------------
// API DTOs (src/dark_factory/api/dto.py)
// ---------------------------------------------------------------------------

export interface RunSummary {
  run_id: string;
  change_id: string;
  route: Route;
  provider: Provider;
  status: RunStatus;
  state_revision: number;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface StageSummary {
  stage: Stage;
  status: StageStatus;
  input_revision: string | null;
  attempt_count: number;
}

export interface UsageAggregate {
  prompt_tokens: number;
  completion_tokens: number;
  /** Decimal over the wire: a string, or null when the cost was never recorded. */
  cost: string | null;
  manual_interventions: number;
}

export interface RunCard extends RunSummary {
  stages: StageSummary[];
  usage: UsageAggregate;
  gates: GateResult[];
  open_blockers: number;
}

export interface TraceStage {
  stage: Stage;
  status: StageStatus;
  input_revision: string | null;
  attempt_number: number;
  produced_at: string;
  artifacts: ArtifactRef[];
}

export interface RunTrace {
  change_id: string;
  run_id: string;
  status: RunStatus;
  chain: TraceStage[];
}

export interface ChangeRunRef {
  run_id: string;
  status: RunStatus;
}

export interface ChangeCard extends Change {
  runs: ChangeRunRef[];
  decisions_count: number;
}

export interface ChangeTrace {
  change_id: string;
  runs: RunTrace[];
}

// ---------------------------------------------------------------------------
// Request bodies
// ---------------------------------------------------------------------------

export interface ApprovalRequest {
  gate: Gate;
  outcome: DecisionOutcome;
  /** Required: the decision is version-bound to this revision (ADR-009 p.7). */
  subject_revision: string;
  comment?: string | null;
  /** Optimistic-concurrency guard (ADR-006 p.4); a mismatch answers 409. */
  expected_state_revision?: number | null;
}

/** RFC 7807-like body of every failed response (api/dto.py ErrorBody). */
export interface ErrorBody {
  type: string;
  title: string;
  status: number;
  detail: string;
}
