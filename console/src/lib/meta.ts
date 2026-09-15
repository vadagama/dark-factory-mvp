/**
 * Typed accessor for the generated meta snapshot (console/src/generated/
 * meta.json, produced by console/tools/export_meta.py from the Python
 * sources of truth — ADR-021 p.5). Regenerate after changing limits or
 * profiles: `uv run python console/tools/export_meta.py`; the drift test
 * (tests/test_console_meta_snapshot.py) keeps the committed file honest.
 */

import rawMeta from "../generated/meta.json";
import type { Gate, Stage } from "../api/types";

export interface MetaLimits {
  max_rework_rounds: number;
  token_budget: number | null;
  cost_budget: string | null;
  deadline: string | null;
}

export interface MetaProfile {
  role: string;
  name: string;
  version: string;
  description: string;
  schema_version: number;
  inputs: string[];
  outputs: string[];
  tools: string[];
  constraints: string[];
  stop_conditions: string[];
  skills: string[];
}

export type FactoryMode = "with_approvals" | "autonomous_until_mr" | "manual";

export interface MetaFactoryMode {
  mode: FactoryMode;
  human_gates: Gate[];
  auto_merge_risk_classes: string[];
  merge_authorization_gate: Gate;
  merge_methods: string[];
  stage_sequence: Stage[];
}

export interface MetaSnapshot {
  schema_version: number;
  limits: MetaLimits;
  profiles: MetaProfile[];
  factory_mode: MetaFactoryMode;
}

export const meta: MetaSnapshot = rawMeta as MetaSnapshot;

/** Human-readable label of the display-only factory mode (ADR-018). */
export function factoryModeLabel(mode: FactoryMode): string {
  switch (mode) {
    case "with_approvals":
      return "С согласованиями";
    case "autonomous_until_mr":
      return "Автономно до MR";
    case "manual":
      return "Ручной режим";
  }
}

/**
 * Expected change `state_revision` derived from the change card (the API
 * does not expose the change's state_revision on reads). Inference from the
 * T035 semantics: a change row starts at state_revision = 1
 * (orchestration/state/change_store.py) and every recorded approval bumps it
 * by exactly one (api/routes_changes.py) — so expected = 1 + decisions_count.
 * The field stays editable in the approval form in case future mutations
 * start bumping the revision too.
 */
export function deriveExpectedStateRevision(decisionsCount: number): number {
  return 1 + decisionsCount;
}
