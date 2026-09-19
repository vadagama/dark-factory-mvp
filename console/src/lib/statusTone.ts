/** Tone of a wire status — drives the badge color via CSS classes. */
export type StatusTone = "success" | "danger" | "warning" | "info" | "neutral" | "muted";

const RUN_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  running: "info",
  waiting: "warning",
  blocked: "danger",
  succeeded: "success",
  failed: "danger",
  canceled: "muted",
  superseded: "muted",
};

const STAGE_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  in_progress: "info",
  waiting: "warning",
  succeeded: "success",
  failed: "danger",
  blocked: "danger",
  skipped: "muted",
  superseded: "muted",
  canceled: "muted",
};

const GATE_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  passed: "success",
  failed: "danger",
  skipped: "muted",
};

const DECISION_TONES: Record<string, StatusTone> = {
  approved: "success",
  rejected: "danger",
  waived: "warning",
};

const SEVERITY_TONES: Record<string, StatusTone> = {
  blocker: "danger",
  major: "warning",
  minor: "info",
  info: "neutral",
};

/** Product readiness (ADR-030 p.1): the failure cause is text, the tone only flags it. */
const PRODUCT_TONES: Record<string, StatusTone> = {
  created: "neutral",
  validating: "info",
  ready: "success",
  error: "danger",
};

/** Brief completeness (T071): a draft is a warning — requirements cannot start from it. */
const BRIEF_TONES: Record<string, StatusTone> = {
  draft: "warning",
  complete: "success",
};

/** Observed repository state (ADR-031 p.4). */
const REPOSITORY_TONES: Record<string, StatusTone> = {
  unavailable: "danger",
  empty: "warning",
  baseline_absent: "warning",
  baseline_current: "success",
  baseline_stale: "warning",
};

const TABLES: Record<string, Record<string, StatusTone>> = {
  run: RUN_TONES,
  stage: STAGE_TONES,
  gate: GATE_TONES,
  decision: DECISION_TONES,
  severity: SEVERITY_TONES,
  product: PRODUCT_TONES,
  brief: BRIEF_TONES,
  repository: REPOSITORY_TONES,
};

export type StatusKind =
  | "run"
  | "stage"
  | "gate"
  | "decision"
  | "severity"
  | "product"
  | "brief"
  | "repository"
  | "generic";

/** One mapping per wire enum keeps surprising values visible (neutral). */
export function statusTone(status: string, kind: StatusKind): StatusTone {
  return TABLES[kind]?.[status] ?? "neutral";
}
