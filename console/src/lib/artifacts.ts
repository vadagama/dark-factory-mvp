/**
 * ChangeSet workspace helpers (T088–T090, ADR-032/ADR-034/ADR-035): pure,
 * testable functions with no domain "next step" logic — phases and their
 * labels, artifact path encoding, the frontmatter split used by the editor to
 * render the body, anchor lookup for a text selection and the labels of the
 * server's phase states. The left column renders `GET /changes/{id}/phases`
 * as is — nothing here computes a phase state or a percentage.
 */

import type { ArtifactKind, Gate, Phase, PhaseViewState, UiStateKind } from "../api/types";
import type { StatusTone } from "./statusTone";

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
  architecture: null,
  interface: null,
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

/** Operator labels of the server phase states (`PhaseView.state`, T098); the state itself is never computed here. */
export const PHASE_VIEW_STATE_LABELS: Record<PhaseViewState, string> = {
  pending: "не начата",
  active: "в работе",
  needs_decision: "ждёт решения",
  approved: "согласована",
  waived: "пропущена",
  not_required: "не требуется",
  stale: "неактуально",
  done: "пройдена",
  blocked: "заблокирована",
};

/** Colour only for states that need attention (ADR-037 p.8); the label always carries the meaning. */
export const PHASE_VIEW_STATE_TONES: Record<PhaseViewState, StatusTone> = {
  pending: "muted",
  active: "info",
  needs_decision: "warning",
  approved: "success",
  waived: "muted",
  not_required: "muted",
  stale: "warning",
  done: "neutral",
  blocked: "danger",
};

/** The five screen states of a UI spec in the order the gallery shows them (T094). */
export const UI_STATE_KINDS: readonly UiStateKind[] = ["loading", "empty", "error", "success", "access"];

export const UI_STATE_LABELS: Record<UiStateKind, string> = {
  loading: "загрузка",
  empty: "пусто",
  error: "ошибка",
  success: "успех",
  access: "доступ",
};

/**
 * The link «Открыть на dev» of a screen: an absolute `preview_url` as is, a
 * relative one joined to the product `dev_url`; null when there is nothing
 * to open (no `preview_url`, or a relative one without a dev environment) —
 * the gallery then says so instead of linking nowhere.
 */
export function previewHref(previewUrl: string | null, devUrl: string | null): string | null {
  if (!previewUrl) {
    return null;
  }
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(previewUrl)) {
    return previewUrl;
  }
  if (!devUrl) {
    return null;
  }
  return `${devUrl.replace(/\/+$/, "")}/${previewUrl.replace(/^\/+/, "")}`;
}

/** Sum of the recorded run costs as a decimal string (4 places), or null when nothing was recorded. */
export function budgetFact(costs: readonly (string | null)[]): string | null {
  const recorded = costs.filter((cost): cost is string => cost !== null);
  if (recorded.length === 0) {
    return null;
  }
  return recorded.reduce((sum, cost) => sum + Number(cost), 0).toFixed(4);
}
