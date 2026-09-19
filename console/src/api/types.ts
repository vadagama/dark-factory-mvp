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
/** Scope chosen at intake (T071): stop after the specs are approved, or go to dev. */
export type Scenario = "specs_only" | "full";
/** Derived server-side: `complete` iff `problem` and `goal` are non-empty. */
export type BriefStatus = "draft" | "complete";
export type BriefAuthor = "operator" | "agent";
/** Readiness of a product repository (ADR-030 p.1); the cause lives in `status_reason`. */
export type ProductStatus = "created" | "validating" | "ready" | "error";
/** Observed repository state (ADR-031 p.4) — never a request. */
export type RepositoryState =
  | "unavailable"
  | "empty"
  | "baseline_absent"
  | "baseline_current"
  | "baseline_stale";
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

/**
 * Structured brief of a change (changes/intake.py, T071/T072). `status` is
 * derived by the server; `source_text` keeps the operator's wording next to
 * the structured fields; `error` is the last reason the agent could not
 * formulate (observable, T072 DoD).
 */
export interface IntakeBrief {
  problem: string | null;
  goal: string | null;
  constraints: string[];
  out_of_scope: string[];
  source_text: string | null;
  status: BriefStatus;
  formulated_by: BriefAuthor | null;
  error: string | null;
}

/** Hard spend limit chosen at intake (T071): USD as a decimal string, tokens optional. */
export interface SpendLimit {
  cost_budget_usd: string;
  token_budget: number | null;
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
  /** Owning product (ADR-030); null for pre-T065 changes. */
  product_id: string | null;
  /** Intake brief (T071); null for pre-T071 changes. */
  brief: IntakeBrief | null;
  scenario: Scenario;
  spend_limit: SpendLimit | null;
}

// ---------------------------------------------------------------------------
// Products (changes/product.py, ports/provisioning.py — T065/T066, ADR-030/031)
// ---------------------------------------------------------------------------

export interface Product {
  id: string;
  name: string;
  description: string | null;
  repository: RepositoryRef;
  repository_url: string | null;
  baseline_ref: string | null;
  dev_env_ref: string | null;
  status: ProductStatus;
  status_reason: string | null;
  state_revision: number;
  created_at: string;
}

/** Read-only result of a repository validation (mutates nothing, ADR-031 p.5). */
export interface RepositoryValidation {
  repository: RepositoryRef;
  state: RepositoryState;
  default_branch: string | null;
  head_revision: string | null;
}

// ---------------------------------------------------------------------------
// Guidance (orchestration/guidance.py — T074, ADR-033): the operator's next
// step as a server-computed read model. The Console only renders it.
// ---------------------------------------------------------------------------

export type GuidanceActor = "operator" | "agent" | "ci" | "factory" | "external";
export type GuidancePhase =
  | "initiative"
  | "requirements"
  | "architecture"
  | "interface"
  | "plan"
  | "execution"
  | "demonstration"
  | "delivery"
  | "done";

export interface GuidanceSubject {
  kind: "product" | "change";
  id: string;
}

/** One declarative action; `api` looks like "POST /products/prd-1/validate". */
export interface GuidanceAction {
  label: string;
  cli: string | null;
  api: string | null;
  enabled: boolean;
  reason: string | null;
}

export interface GuidanceBlocker {
  what: string;
  who: GuidanceActor;
  how: string;
}

export interface Guidance {
  schema_version: 1;
  subject: GuidanceSubject;
  phase: GuidancePhase | null;
  headline: string;
  why: string;
  primary: GuidanceAction;
  secondary: GuidanceAction[];
  blockers: GuidanceBlocker[];
  after: string | null;
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

/** `POST /products/{id}/validate`: the product plus the raw observation. */
export interface ProductValidationView extends Product {
  validation: RepositoryValidation | null;
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

/** `POST /products` (T066): id is client-chosen so a retry replays instead of duplicating. */
export interface ProductCreateRequest {
  id: string;
  name: string;
  repository: RepositoryRef;
  description?: string | null;
  repository_url?: string | null;
  baseline_ref?: string | null;
  dev_env_ref?: string | null;
}

/** `POST /briefs/formulate` (T072): the operator's free text, at least one char. */
export interface BriefFormulateRequest {
  source_text: string;
}

/** RFC 7807-like body of every failed response (api/dto.py ErrorBody). */
export interface ErrorBody {
  type: string;
  title: string;
  status: number;
  detail: string;
}

// ---------------------------------------------------------------------------
// CI stages (T058/ADR-026 — the `CI_SKIP_<JOB>` repository variables)
// ---------------------------------------------------------------------------

export type CiStageGroup = "python" | "factory" | "console" | "uikit" | "image";
export type CiStageWeight = "light" | "medium" | "heavy";

/** One switchable CI stage: an element of `stages` and the PUT response. */
export interface CiStage {
  job: string;
  title: string;
  group: CiStageGroup;
  summary: string;
  local_command: string;
  weight: CiStageWeight;
  variable: string;
  /** true = the stage runs; false = switched off; null = unknown (`available: false`). */
  enabled: boolean | null;
}

/** `GET /ci/stages`: the toggles of the repository whose CI is controlled. */
export interface CiStages {
  schema_version: 1;
  /** `owner/name` of the controlled repository; null when `available` is false. */
  repository: string | null;
  available: boolean;
  /** Human-readable English reason when `available` is false (GitHub unreachable). */
  reason: string | null;
  stages: CiStage[];
}

// ---------------------------------------------------------------------------
// Conversations (changes/conversations.py, changes/enums.py — T086, ADR-034):
// questions, answers, comments, rework orders. Wire strings are lowercase.
// ---------------------------------------------------------------------------

/** Operator phase of a ChangeSet (ADR-032): F0..F8 plus `done`; same values as `GuidancePhase`. */
export type Phase = GuidancePhase;
export type AnswerKind = "choice" | "text" | "number";
/** `open → answered → resolved | stale` (ADR-034 p.1). */
export type QuestionStatus = "open" | "answered" | "resolved" | "stale";
/** `addressed` is the agent's «исправлено» — never a closure; only the operator closes. */
export type CommentStatus = "open" | "addressed" | "closed";
/** Read-model fact: a `detached` anchor is shown explicitly and never re-attached. */
export type AnchorState = "attached" | "detached";
export type ReworkOrderStatus = "pending" | "in_progress" | "done" | "escalated";
/** How a recorded approval relates to the current revision (ADR-035 p.7). */
export type RevisionState = "current" | "stale" | "unbound";

/** `artifact + anchor_id + revision` — where a question or a comment points (ADR-034 p.1). */
export interface ArtifactAnchor {
  artifact: string;
  anchor_id: string | null;
  revision: string | null;
}

export interface Answer {
  value: string;
  comment: string | null;
  answered_by: string;
  answered_at: string;
}

export interface Question {
  id: string;
  change_id: string;
  phase: Phase;
  run_id: string | null;
  asked_by: Role | null;
  text: string;
  kind: AnswerKind;
  options: string[];
  anchor: ArtifactAnchor | null;
  /** `false` = an assumption: the gate stays available while it is open (T087). */
  blocking: boolean;
  status: QuestionStatus;
  answer: Answer | null;
  asked_at: string;
  updated_at: string;
}

export interface Comment {
  id: string;
  change_id: string;
  phase: Phase;
  anchor: ArtifactAnchor;
  body: string;
  author: string;
  status: CommentStatus;
  rework_order_id: string | null;
  addressed_note: string | null;
  created_at: string;
  updated_at: string;
}

/** `GET /changes/{id}/comments` item: the comment plus its anchor state at the head. */
export interface CommentView extends Comment {
  anchor_state: AnchorState;
}

/** The agent's report after a rework round: what changed, what remains. */
export interface ReworkSummary {
  changed: string[];
  remaining: string[];
  addressed_comment_ids: string[];
}

export interface ReworkOrder {
  id: string;
  change_id: string;
  phase: Phase;
  /** Artifact path → revision the order was issued against. */
  revisions: Record<string, string>;
  comment_ids: string[];
  question_ids: string[];
  instruction: string | null;
  issued_by: string;
  status: ReworkOrderStatus;
  round: number | null;
  run_id: string | null;
  summary: ReworkSummary | null;
  escalation_reason: string | null;
  created_at: string;
  updated_at: string;
}

// Phase gate (orchestration/phase_gate.py — T087): why the gate is closed and how to open it.

export interface GateReason {
  what: string;
  how: string;
}

export interface ApprovalView {
  decision_id: string;
  outcome: DecisionOutcome;
  revision: string | null;
  state: RevisionState;
  comment: string | null;
}

export interface PhaseGate {
  change_id: string;
  phase: Phase;
  gate: Gate | null;
  available: boolean;
  reasons: GateReason[];
  current_revision: string | null;
  /** A *current* approval exists; a view mark or a stale approval never counts. */
  approved: boolean;
  skippable: boolean;
  approvals: ApprovalView[];
  blocking_questions: number;
  open_questions: number;
  answered_questions: number;
  open_comments: number;
  addressed_comments: number;
  detached_comments: number;
  rework_pending: boolean;
  rework_in_progress: boolean;
  rework_rounds_used: number;
  rework_rounds_max: number;
}

// ---------------------------------------------------------------------------
// Artifacts (context/artifacts.py, api/dto.py — T082–T085, ADR-035): git is the
// source of truth; the Console reads documents by revision and writes commits.
// ---------------------------------------------------------------------------

export type ArtifactKind = "spec" | "design" | "adr" | "ui" | "plan" | "other";

export interface ArtifactNode {
  path: string;
  kind: ArtifactKind;
  revision: string | null;
}

/** `GET /changes/{id}/artifacts`: `revision === null` means no branch yet (≠ an empty branch). */
export interface ArtifactTreeView {
  change_id: string;
  branch: string;
  revision: string | null;
  nodes: ArtifactNode[];
  /** Paths that have an autosave draft. */
  drafts: string[];
}

/** Frontmatter as data (ADR-035 p.5): values and the keys the editor must not change. */
export interface DocumentProperties {
  values: Record<string, unknown>;
  protected: string[];
}

export interface ArtifactDraftView {
  change_id: string;
  artifact: string;
  content: string;
  base_revision: string | null;
  saved_by: string;
  updated_at: string;
  /** The branch head moved away from `base_revision`. */
  stale: boolean;
}

/** `GET /changes/{id}/artifacts/{path}`: one document at one revision with its context. */
export interface ArtifactDocumentView {
  path: string;
  kind: ArtifactKind;
  /** A commit SHA — never `latest`. */
  revision: string;
  /** The whole file, verbatim (frontmatter included) — the editor's source of truth. */
  content: string;
  properties: DocumentProperties | null;
  body: string;
  /** Stable ids a comment or a question may bind to, in document order. */
  anchors: string[];
  frontmatter_error: string | null;
  draft: ArtifactDraftView | null;
  /** The operator viewed *this* revision — «просмотрено» ≠ «согласовано» (ADR-034 p.2). */
  viewed: boolean;
  open_comments: number;
  open_questions: number;
}

/** `PUT /changes/{id}/artifacts/{path}`: the new revision and the staleness effects. */
export interface ArtifactWriteView {
  path: string;
  revision: string;
  previous_revision: string | null;
  created_commit: boolean;
  stale_questions: string[];
  detached_comments: string[];
}

export interface ArtifactRevision {
  revision: string;
  message: string;
  author: string | null;
  authored_at: string | null;
}

export interface ArtifactDiff {
  path: string;
  from_revision: string;
  to_revision: string;
  unified: string;
  added: number;
  removed: number;
}

// Request bodies (api/dto.py)

export interface AnswerRequest {
  value: string;
  comment?: string | null;
}

export interface CommentCreateRequest {
  artifact: string;
  anchor_id?: string | null;
  revision?: string | null;
  body: string;
  phase?: Phase | null;
}

export interface ReworkOrderRequest {
  phase?: Phase | null;
  comment_ids: string[];
  question_ids: string[];
  instruction?: string | null;
  expected_revision?: string | null;
}

export interface ArtifactEditRequest {
  content: string;
  base_revision: string | null;
  message?: string | null;
  /** Frontmatter values replaced on top of `content`; protected keys are refused with 422. */
  properties?: Record<string, unknown> | null;
}

export interface ArtifactDraftRequest {
  content: string;
  base_revision: string | null;
}

export interface ArtifactViewRequest {
  revision: string;
}
