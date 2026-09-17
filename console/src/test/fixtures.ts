/**
 * Synthetic API fixtures for unit tests (ADR-021 p.4: no secrets, no real
 * tokens — only clearly synthetic data shaped like the T035 wire contract).
 */

import type {
  Change,
  ChangeCard,
  ChangeTrace,
  CiStage,
  CiStages,
  Decision,
  Evidence,
  Finding,
  GateResult,
  RunCard,
  RunSummary,
  RunTrace,
  StageSummary,
  UsageAggregate,
} from "../api/types";

export const change: Change = {
  id: "chg_demo_001",
  title: "Add /health endpoint",
  description: null,
  source: "console",
  external_ref: null,
  product: { provider: "github", slug: "acme/demo-service" },
  risk_class: "R2",
  change_request: null,
  created_at: "2026-09-01T10:00:00Z",
};

export const runSummary: RunSummary = {
  run_id: "run_demo_001",
  change_id: "chg_demo_001",
  route: "standard",
  provider: "github",
  status: "succeeded",
  state_revision: 2,
  created_at: "2026-09-01T10:01:00Z",
  updated_at: "2026-09-01T10:15:00Z",
  finished_at: "2026-09-01T10:15:00Z",
};

export const runSummarySecond: RunSummary = {
  ...runSummary,
  run_id: "run_demo_002",
  change_id: "chg_demo_002",
  status: "running",
  state_revision: 1,
  finished_at: null,
  updated_at: "2026-09-01T10:20:00Z",
};

export const stageSummaries: StageSummary[] = [
  { stage: "specification", status: "succeeded", input_revision: null, attempt_count: 1 },
  { stage: "planning", status: "succeeded", input_revision: "abc1234", attempt_count: 1 },
  { stage: "construction", status: "succeeded", input_revision: "abc1234", attempt_count: 2 },
  { stage: "review_verification", status: "succeeded", input_revision: "def5678", attempt_count: 1 },
  { stage: "release", status: "succeeded", input_revision: "def5678", attempt_count: 1 },
];

export const usageAggregate: UsageAggregate = {
  prompt_tokens: 12500,
  completion_tokens: 4300,
  cost: "0.2847",
  manual_interventions: 1,
};

export const gateResult: GateResult = {
  gate: "review",
  status: "passed",
  sha: "abc1234def",
  summary: "2 approvals",
  evidence_ids: ["ev_001"],
};

export const blockerFinding: Finding = {
  id: "fnd_001",
  origin: "agent",
  role: "quality",
  severity: "blocker",
  category: "tests",
  file: "src/api.py",
  line: 42,
  reviewed_sha: "abc1234",
  required_action: "Fix failing integration tests",
  status: "open",
  confidence: 0.9,
  evidence_ids: [],
};

export const decision: Decision = {
  id: "dec_abc123",
  gate: "review",
  outcome: "approved",
  decided_by: "human",
  role: null,
  decided_at: "2026-09-01T11:00:00Z",
  commit_sha: "abc1234def",
  comment: "ok",
  evidence_ids: [],
};

export const evidenceItem: Evidence = {
  id: "ev_001",
  type: "test_results",
  uri: "https://ci.example.com/jobs/1",
  checksum: null,
  produced_at: "2026-09-01T10:14:00Z",
  required: true,
  available: true,
};

export const runCard: RunCard = {
  ...runSummary,
  stages: stageSummaries,
  usage: usageAggregate,
  gates: [gateResult],
  open_blockers: 1,
};

export const runCardSecond: RunCard = {
  ...runSummarySecond,
  stages: [
    { stage: "specification", status: "succeeded", input_revision: null, attempt_count: 1 },
    { stage: "planning", status: "in_progress", input_revision: "abc1234", attempt_count: 1 },
  ],
  usage: {
    prompt_tokens: 900,
    completion_tokens: 100,
    cost: null,
    manual_interventions: 0,
  },
  gates: [{ gate: "specification", status: "passed", sha: null, summary: null, evidence_ids: [] }],
  open_blockers: 0,
};

export function makeChangeCard(decisionsCount: number): ChangeCard {
  return {
    ...change,
    runs: [{ run_id: "run_demo_001", status: "succeeded" }],
    decisions_count: decisionsCount,
  };
}

export const changeCard: ChangeCard = makeChangeCard(1);

export const runTrace: RunTrace = {
  change_id: "chg_demo_001",
  run_id: "run_demo_001",
  status: "succeeded",
  chain: [
    { stage: "specification", status: "succeeded", input_revision: null, attempt_number: 1, produced_at: "2026-09-01T10:03:00Z", artifacts: [] },
    { stage: "planning", status: "succeeded", input_revision: "abc1234", attempt_number: 1, produced_at: "2026-09-01T10:06:00Z", artifacts: [] },
    { stage: "construction", status: "succeeded", input_revision: "abc1234", attempt_number: 2, produced_at: "2026-09-01T10:10:00Z", artifacts: [] },
    { stage: "review_verification", status: "succeeded", input_revision: "def5678", attempt_number: 1, produced_at: "2026-09-01T10:13:00Z", artifacts: [] },
    { stage: "release", status: "succeeded", input_revision: "def5678", attempt_number: 1, produced_at: "2026-09-01T10:15:00Z", artifacts: [] },
  ],
};

export const changeTrace: ChangeTrace = {
  change_id: "chg_demo_001",
  runs: [runTrace],
};

export const ciStage: CiStage = {
  job: "lint",
  title: "Ruff lint + format",
  group: "python",
  summary: "Стиль, импорты и форматирование (ruff check и ruff format --check).",
  local_command: "uv run ruff check . && uv run ruff format --check .",
  weight: "light",
  variable: "CI_SKIP_LINT",
  enabled: true,
};

/** A stage that is currently switched off — the bulk action has to pick it up. */
export const ciStageOff: CiStage = {
  job: "console-e2e",
  title: "Playwright smoke",
  group: "console",
  summary: "Сборка, vite preview и smoke-сценарии с установкой Chromium.",
  local_command: "cd console && npx playwright install chromium && npm run e2e",
  weight: "heavy",
  variable: "CI_SKIP_CONSOLE_E2E",
  enabled: false,
};

export const ciStages: CiStages = {
  schema_version: 1,
  repository: "vadagama/dark-factory-mvp",
  available: true,
  reason: null,
  stages: [ciStage, ciStageOff],
};

/** The API cannot reach GitHub: `repository` is null, every state is unknown. */
export const ciStagesUnavailable: CiStages = {
  schema_version: 1,
  repository: null,
  available: false,
  reason: "GitHub credentials are not configured on this contour",
  stages: [{ ...ciStage, enabled: null }],
};
