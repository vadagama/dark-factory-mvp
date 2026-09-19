/**
 * Guidance rendering helpers (T075, ADR-033). The server computes the next
 * step; the Console only decides *how* to surface each action: an `api`
 * string it knows how to perform becomes a button, anything else is shown as
 * the CLI command — the two surfaces always name the same step (ADR-033 p.3).
 * No "next step" logic lives here: only a parser of the server's own strings.
 */

import type { GuidanceAction, GuidanceActor, GuidanceSubject, Phase } from "../api/types";

/**
 * Actions the Console can perform itself (M1 + the M2 ChangeSet workspace);
 * the rest — run advance/withdraw — is CLI until M4.
 */
export type ConsoleAction =
  | { kind: "validate_product"; productId: string }
  | { kind: "reload_product"; productId: string }
  | { kind: "new_change"; productId: string | null }
  | { kind: "edit_brief"; changeId: string }
  | { kind: "open_change"; changeId: string }
  | { kind: "open_run"; runId: string }
  /**
   * `POST /changes/{id}/approvals` — the phase approval form (T088 decision
   * panel). `phase` is read from the server's own CLI hint
   * (`approve … --phase architecture|interface`, M3) when it names one.
   */
  | { kind: "approve_phase"; changeId: string; phase?: Phase }
  /** «Подтвердить пропуск UI» (M3, T097): `waived` on gate `ui`, phase `interface`, reason prefilled. */
  | { kind: "waive_phase"; changeId: string; phase: "interface"; gate: "ui" }
  /** `POST /changes/{id}/decisions/{decision_id}/alternative` (M3, T093): brings the decisions overview into view. */
  | { kind: "request_alternative"; changeId: string; decisionId: string | null }
  /** `POST /changes/{id}/rework-orders` — the send-back form (T089). */
  | { kind: "rework"; changeId: string }
  /** `GET /changes/{id}/questions?status=open` — focus the open questions (T089). */
  | { kind: "focus_questions"; changeId: string }
  /** `GET /changes/{id}/artifacts` — open the artifacts of the phase (T089). */
  | { kind: "open_artifacts"; changeId: string };

const PATTERNS: [RegExp, (match: RegExpMatchArray, subject: GuidanceSubject) => ConsoleAction][] = [
  [/^POST \/products\/([^/\s]+)\/validate$/, (m) => ({ kind: "validate_product", productId: m[1] })],
  [/^GET \/products\/([^/\s]+)$/, (m) => ({ kind: "reload_product", productId: m[1] })],
  [
    /^POST \/changes$/,
    (_m, subject) => ({ kind: "new_change", productId: subject.kind === "product" ? subject.id : null }),
  ],
  [/^PUT \/changes\/([^/\s]+)\/brief$/, (m) => ({ kind: "edit_brief", changeId: m[1] })],
  [/^GET \/changes\/([^/\s]+)$/, (m) => ({ kind: "open_change", changeId: m[1] })],
  [/^GET \/runs\/([^/\s]+)$/, (m) => ({ kind: "open_run", runId: m[1] })],
  [
    /^POST \/changes\/([^/\s]+)\/decisions\/([^/\s]+)\/alternative$/,
    (m) => ({ kind: "request_alternative", changeId: m[1], decisionId: m[2] }),
  ],
  [/^POST \/changes\/([^/\s]+)\/approvals$/, (m) => ({ kind: "approve_phase", changeId: m[1] })],
  [/^POST \/changes\/([^/\s]+)\/rework-orders$/, (m) => ({ kind: "rework", changeId: m[1] })],
  [/^GET \/changes\/([^/\s]+)\/questions(?:\?status=open)?$/, (m) => ({ kind: "focus_questions", changeId: m[1] })],
  [/^GET \/changes\/([^/\s]+)\/artifacts$/, (m) => ({ kind: "open_artifacts", changeId: m[1] })],
];

/** The phase names the server puts after `--phase` in its CLI hints (M3 approvals). */
const APPROVE_PHASE = /\bapprove\b.*--phase[\s=](architecture|interface)\b/;
/** Server labels that name Console-specific intents (the `api` alone is ambiguous for them). */
const WAIVE_UI_LABEL = /подтвердить пропуск ui/i;
const ALTERNATIVE_LABEL = /запросить альтернативу/i;

/** The parts of a `GuidanceAction` the parser may look at besides `api`. */
export type GuidanceHints = Pick<GuidanceAction, "cli" | "label">;

/**
 * Maps a server `api` string ("POST /products/prd-1/validate") to a Console
 * action; null when the Console has no equivalent yet (e.g. run advance,
 * withdraw — execution from the Console comes in M4) and the CLI is the way.
 * Ids are URL-decoded in place: the server writes them raw. `hints` (the
 * action's `cli` and `label`) only refine an action the `api` already names:
 * `approve … --phase architecture|interface` fixes the phase of an approval,
 * «Подтвердить пропуск UI» turns the same approval endpoint into the explicit
 * waiver, «Запросить альтернативу» points at the decisions overview.
 */
export function parseGuidanceApi(api: string | null, subject: GuidanceSubject, hints?: GuidanceHints | null): ConsoleAction | null {
  if (!api) {
    return null;
  }
  const trimmed = api.trim();
  for (const [pattern, build] of PATTERNS) {
    const match = trimmed.match(pattern);
    if (match) {
      return refine(
        build(match.map((part) => (part === undefined ? part : safeDecode(part))) as RegExpMatchArray, subject),
        hints ?? null,
      );
    }
  }
  return null;
}

function refine(action: ConsoleAction, hints: GuidanceHints | null): ConsoleAction {
  if (!hints || action.kind !== "approve_phase") {
    return action;
  }
  if (hints.label && WAIVE_UI_LABEL.test(hints.label)) {
    return { kind: "waive_phase", changeId: action.changeId, phase: "interface", gate: "ui" };
  }
  if (hints.label && ALTERNATIVE_LABEL.test(hints.label)) {
    return { kind: "request_alternative", changeId: action.changeId, decisionId: null };
  }
  const phase = hints.cli?.match(APPROVE_PHASE)?.[1] as "architecture" | "interface" | undefined;
  return phase ? { ...action, phase } : action;
}

function safeDecode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/** Who removes a blocker (ADR-033 p.4), in the operator's language. */
export const ACTOR_LABELS: Record<GuidanceActor, string> = {
  operator: "оператор",
  agent: "агент",
  ci: "CI",
  factory: "фабрика",
  external: "внешняя система",
};

export function actorLabel(actor: GuidanceActor): string {
  return ACTOR_LABELS[actor] ?? actor;
}
