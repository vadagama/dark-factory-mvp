/**
 * Brief form helpers (T071/T072/T076): the structured fields are edited as
 * text (one constraint per line), the wire model wants string arrays; the
 * author is recorded honestly — `agent` only while the agent's wording is
 * unchanged, `operator` as soon as a field is edited by hand.
 */

import type { BriefAuthor, BriefStatus, IntakeBrief } from "../api/types";

/** The four editable fields of a brief as form text. */
export interface BriefFields {
  problem: string;
  goal: string;
  /** One entry per line. */
  constraints: string;
  /** One entry per line. */
  out_of_scope: string;
}

export const EMPTY_BRIEF_FIELDS: BriefFields = { problem: "", goal: "", constraints: "", out_of_scope: "" };

/** Splits a textarea into trimmed, non-empty lines (order preserved — the operator's order matters). */
export function splitLines(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export function briefToFields(brief: IntakeBrief | null): BriefFields {
  if (!brief) {
    return EMPTY_BRIEF_FIELDS;
  }
  return {
    problem: brief.problem ?? "",
    goal: brief.goal ?? "",
    constraints: brief.constraints.join("\n"),
    out_of_scope: brief.out_of_scope.join("\n"),
  };
}

/** Mirrors the server derivation (changes/intake.py): complete iff problem and goal are stated. */
export function deriveBriefStatus(fields: BriefFields): BriefStatus {
  return fields.problem.trim() && fields.goal.trim() ? "complete" : "draft";
}

/** True when the operator has not touched the agent's wording. */
export function sameBriefFields(a: BriefFields, b: BriefFields): boolean {
  return (
    a.problem.trim() === b.problem.trim() &&
    a.goal.trim() === b.goal.trim() &&
    splitLines(a.constraints).join("\n") === splitLines(b.constraints).join("\n") &&
    splitLines(a.out_of_scope).join("\n") === splitLines(b.out_of_scope).join("\n")
  );
}

/**
 * Who wrote the fields being submitted: `agent` when an agent formulation
 * exists and is unchanged, `operator` otherwise (edited, or never formulated).
 */
export function briefAuthor(fields: BriefFields, agentFields: BriefFields | null): BriefAuthor {
  return agentFields !== null && sameBriefFields(fields, agentFields) ? "agent" : "operator";
}

/** Builds the wire brief from form text; `status` is derived like the server does. */
export function fieldsToBrief(
  fields: BriefFields,
  options: { source_text: string | null; formulated_by: BriefAuthor; error?: string | null },
): IntakeBrief {
  const problem = fields.problem.trim();
  const goal = fields.goal.trim();
  return {
    problem: problem ? problem : null,
    goal: goal ? goal : null,
    constraints: splitLines(fields.constraints),
    out_of_scope: splitLines(fields.out_of_scope),
    source_text: options.source_text?.trim() ? options.source_text.trim() : null,
    status: deriveBriefStatus(fields),
    formulated_by: options.formulated_by,
    error: options.error ?? null,
  };
}

/** Positive decimal with up to 4 places (SpendLimit.cost_budget_usd: gt=0, decimal_places=4). */
export function parseCostBudget(text: string): string | null {
  const trimmed = text.trim().replace(",", ".");
  if (!/^\d{1,8}(\.\d{1,4})?$/.test(trimmed)) {
    return null;
  }
  if (Number(trimmed) <= 0) {
    return null;
  }
  return trimmed;
}

/** Optional positive integer (SpendLimit.token_budget: ge=1); "" → null, invalid → undefined. */
export function parseTokenBudget(text: string): number | null | undefined {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  if (!/^\d+$/.test(trimmed)) {
    return undefined;
  }
  const value = Number(trimmed);
  return value >= 1 ? value : undefined;
}
