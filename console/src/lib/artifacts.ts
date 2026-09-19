/**
 * ChangeSet workspace helpers (T088–T090, ADR-032/ADR-034/ADR-035): pure,
 * testable functions with no domain "next step" logic — phases and their
 * labels, artifact path encoding, the frontmatter split used by the editor to
 * render the body, anchor lookup for a text selection and the phase-state
 * projection of the left column. Nothing here computes a percentage.
 */

import type { ArtifactKind, Gate, Phase, PhaseGate } from "../api/types";

/** The operator phases F0–F7 of the left column (ADR-032); `done` is not a column entry. */
export const PHASES: readonly Phase[] = [
  "initiative",
  "requirements",
  "architecture",
  "interface",
  "plan",
  "execution",
  "demonstration",
  "delivery",
];

export const PHASE_LABELS: Record<Phase, string> = {
  initiative: "Инициатива",
  requirements: "Требования",
  architecture: "Архитектура",
  interface: "Интерфейс",
  plan: "План",
  execution: "Исполнение",
  demonstration: "Демонстрация",
  delivery: "Доставка",
  done: "Завершено",
};

/** F-number of a phase as the left column shows it («Ф1 · Требования»). */
export function phaseIndexLabel(phase: Phase): string {
  const index = PHASES.indexOf(phase);
  return index === -1 ? "—" : `Ф${index}`;
}

export function phaseLabel(phase: Phase | null): string {
  return phase ? (PHASE_LABELS[phase] ?? phase) : "—";
}

/** The milestone that brings a phase to the Console — honest placeholders for the rest (ADR-037 p.9). */
export const PHASE_MILESTONE: Record<Phase, string | null> = {
  initiative: null,
  requirements: null,
  architecture: "M3",
  interface: "M3",
  plan: "M4",
  execution: "M4",
  demonstration: "M5",
  delivery: "M5",
  done: null,
};

/** The gate a phase approval records against (mirror of `phase_gate.PHASE_GATE`). */
export const PHASE_GATE: Record<Phase, Gate | null> = {
  initiative: null,
  requirements: "specification",
  architecture: "specification",
  interface: "ui",
  plan: "planning",
  execution: "code",
  demonstration: "verification",
  delivery: "release",
  done: null,
};

export const ARTIFACT_KIND_LABELS: Record<ArtifactKind, string> = {
  spec: "спецификация",
  design: "дизайн",
  adr: "ADR",
  ui: "UI",
  plan: "план",
  other: "прочее",
};

/**
 * Encode an artifact path for the URL: each segment percent-encoded, the
 * slashes kept — the server route is `{path:path}`.
 */
export function encodeArtifactPath(path: string): string {
  return path
    .split("/")
    .filter((segment) => segment.length > 0)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

/** File name of an artifact path (`…/REQ-001-percent.md` → `REQ-001-percent.md`). */
export function artifactName(path: string): string {
  const parts = path.split("/").filter((segment) => segment.length > 0);
  return parts[parts.length - 1] ?? path;
}

/** Short commit SHA for display; the full value stays in the title/tooltip. */
export function shortRevision(revision: string | null): string {
  if (!revision) {
    return "—";
  }
  return revision.length > 12 ? revision.slice(0, 12) : revision;
}

/**
 * Split a markdown document into its raw frontmatter block and its body
 * without interpreting either — the editor renders `body` and never rewrites
 * `content` from this split (ADR-035 p.6). A document that does not start
 * with `---` has no frontmatter; an unterminated block is treated as body so
 * nothing is hidden.
 */
export function splitFrontmatter(content: string): { frontmatter: string | null; body: string } {
  if (!content.startsWith("---\n") && content !== "---") {
    return { frontmatter: null, body: content };
  }
  const lines = content.split("\n");
  for (let index = 1; index < lines.length; index += 1) {
    if (lines[index] === "---" || lines[index] === "...") {
      return {
        frontmatter: lines.slice(1, index).join("\n"),
        body: lines.slice(index + 1).join("\n"),
      };
    }
  }
  return { frontmatter: null, body: content };
}

/**
 * The anchor a selection comments on: the first document anchor that occurs
 * inside the selected text (stable ids like `REQ-001` first, since they are
 * what ADR-020 protects), else `null` = the whole document. Never guesses a
 * "nearest" element outside the selection — an anchor must be visible in
 * what the operator selected (ADR-034 p.1: anchors are never moved).
 */
export function anchorForSelection(selection: string, anchors: readonly string[]): string | null {
  const text = selection.trim();
  if (!text) {
    return null;
  }
  const stable = anchors.filter((anchor) => /^[A-Z]{1,4}-\d+/.test(anchor));
  for (const anchor of [...stable, ...anchors]) {
    if (text.includes(anchor)) {
      return anchor;
    }
  }
  const lowered = text.toLowerCase();
  for (const anchor of anchors) {
    // A heading slug matches its heading text: "Цели и границы" ↔ "цели-и-границы".
    if (lowered.replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s+/g, "-").includes(anchor)) {
      return anchor;
    }
  }
  return null;
}

/**
 * Coerce a property field edit back to the type of the original value so a
 * number stays a number and a boolean a boolean; anything else is a string.
 * Objects and arrays are not edited through fields (the source is the way).
 */
export function coercePropertyValue(original: unknown, raw: string): unknown {
  if (typeof original === "number") {
    const parsed = Number(raw);
    return raw.trim() !== "" && Number.isFinite(parsed) ? parsed : raw;
  }
  if (typeof original === "boolean") {
    return raw === "true";
  }
  return raw;
}

/** Whether a property value can be edited as a one-line field. */
export function isScalarProperty(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

export type PhaseState = "approved" | "decision" | "active" | "rework" | "passed" | "pending" | "skipped";

export const PHASE_STATE_LABELS: Record<PhaseState, string> = {
  approved: "согласована",
  decision: "ждёт решения",
  active: "в работе",
  rework: "доработка",
  passed: "пройдена",
  pending: "не начата",
  skipped: "пропущена",
};

/**
 * Projection of a phase's state for the left column from server facts only:
 * the current phase of the guidance and the phase gate. `done` counts as
 * "every phase passed". No percentage is derived anywhere (ADR-037 p.6).
 */
export function phaseState(phase: Phase, current: Phase | null, gate: PhaseGate | null): PhaseState {
  if (gate?.approved) {
    return "approved";
  }
  if (gate?.approvals.some((approval) => approval.outcome === "waived" && approval.state === "current")) {
    return "skipped";
  }
  if (phase === current) {
    if (gate?.rework_pending || gate?.rework_in_progress) {
      return "rework";
    }
    return gate?.available ? "decision" : "active";
  }
  const currentIndex = current === "done" ? PHASES.length : current ? PHASES.indexOf(current) : -1;
  return PHASES.indexOf(phase) < currentIndex ? "passed" : "pending";
}

/** Sum of the recorded run costs as a decimal string (4 places), or null when nothing was recorded. */
export function budgetFact(costs: readonly (string | null)[]): string | null {
  const recorded = costs.filter((cost): cost is string => cost !== null);
  if (recorded.length === 0) {
    return null;
  }
  return recorded.reduce((sum, cost) => sum + Number(cost), 0).toFixed(4);
}
