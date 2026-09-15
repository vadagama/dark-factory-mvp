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

/** One mapping per wire enum keeps surprising values visible (neutral). */
export function statusTone(status: string, kind: "run" | "stage" | "gate" | "decision" | "severity" | "generic"): StatusTone {
  const table =
    kind === "run"
      ? RUN_TONES
      : kind === "stage"
        ? STAGE_TONES
        : kind === "gate"
          ? GATE_TONES
          : kind === "decision"
            ? DECISION_TONES
            : kind === "severity"
              ? SEVERITY_TONES
              : undefined;
  return table?.[status] ?? "neutral";
}
