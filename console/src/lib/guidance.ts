/**
 * Guidance rendering helpers (T075, ADR-033). The server computes the next
 * step; the Console only decides *how* to surface each action: an `api`
 * string it knows how to perform becomes a button, anything else is shown as
 * the CLI command — the two surfaces always name the same step (ADR-033 p.3).
 * No "next step" logic lives here: only a parser of the server's own strings.
 */

import type { GuidanceActor, GuidanceSubject } from "../api/types";

/** Actions the Console can perform itself in M1 (the rest is CLI). */
export type ConsoleAction =
  | { kind: "validate_product"; productId: string }
  | { kind: "reload_product"; productId: string }
  | { kind: "new_change"; productId: string | null }
  | { kind: "edit_brief"; changeId: string }
  | { kind: "open_change"; changeId: string }
  | { kind: "open_run"; runId: string }
  | { kind: "open_gates"; changeId: string };

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
  [/^POST \/changes\/([^/\s]+)\/approvals$/, (m) => ({ kind: "open_gates", changeId: m[1] })],
];

/**
 * Maps a server `api` string ("POST /products/prd-1/validate") to a Console
 * action; null when the Console has no equivalent yet (e.g. run advance,
 * withdraw — execution from the Console comes in M4) and the CLI is the way.
 * Ids are URL-decoded in place: the server writes them raw.
 */
export function parseGuidanceApi(api: string | null, subject: GuidanceSubject): ConsoleAction | null {
  if (!api) {
    return null;
  }
  const trimmed = api.trim();
  for (const [pattern, build] of PATTERNS) {
    const match = trimmed.match(pattern);
    if (match) {
      return build(match.map((part) => (part === undefined ? part : safeDecode(part))) as RegExpMatchArray, subject);
    }
  }
  return null;
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
