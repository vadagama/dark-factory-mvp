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
  Guidance,
  IntakeBrief,
  Product,
  ProductValidationView,
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
  product_id: "prd_demo_001",
  brief: null,
  scenario: "full",
  spend_limit: { cost_budget_usd: "25.0000", token_budget: null },
};

// -- products, briefs, guidance (T065–T076) ----------------------------------

export const product: Product = {
  id: "prd_demo_001",
  name: "Demo service",
  description: "Synthetic product for component tests",
  repository: { provider: "github", slug: "acme/demo-service" },
  repository_url: "https://github.com/acme/demo-service",
  baseline_ref: "baseline/2026-09",
  dev_env_ref: null,
  status: "ready",
  status_reason: null,
  state_revision: 3,
  created_at: "2026-08-30T09:00:00Z",
};

/** Registered but never validated: guidance says "validate". */
export const productCreated: Product = {
  ...product,
  id: "prd_demo_002",
  name: "Legacy API",
  description: null,
  repository: { provider: "gitlab", slug: "acme/legacy-api" },
  repository_url: null,
  baseline_ref: null,
  status: "created",
  state_revision: 1,
  created_at: "2026-09-02T08:00:00Z",
};

export const productValidated: ProductValidationView = {
  ...product,
  status: "ready",
  state_revision: 4,
  validation: {
    repository: product.repository,
    state: "baseline_current",
    default_branch: "main",
    head_revision: "abc1234def",
  },
};

export const briefComplete: IntakeBrief = {
  problem: "Нет проверки живости сервиса",
  goal: "Эндпоинт /health отвечает 200 при готовности",
  constraints: ["Без новых зависимостей"],
  out_of_scope: ["Метрики readiness"],
  source_text: "Нужен health endpoint для демо-сервиса",
  status: "complete",
  formulated_by: "agent",
  error: null,
};

/** The agent harness is not configured: still a 200, but a draft with the cause. */
export const briefDraftWithError: IntakeBrief = {
  problem: null,
  goal: null,
  constraints: [],
  out_of_scope: [],
  source_text: "Нужен health endpoint для демо-сервиса",
  status: "draft",
  formulated_by: null,
  error: "Agent harness is not configured on this contour",
};

export const changeWithBrief: Change = {
  ...change,
  brief: briefComplete,
  scenario: "specs_only",
  spend_limit: { cost_budget_usd: "25.0000", token_budget: 150000 },
};

export const productGuidanceReady: Guidance = {
  schema_version: 1,
  subject: { kind: "product", id: "prd_demo_001" },
  phase: null,
  headline: "Продукт готов к работе",
  why: "Репозиторий проверен фабрикой; задач у продукта: 1.",
  primary: {
    label: "Новая фича",
    cli: "factory change create --product prd_demo_001 …",
    api: "POST /changes",
    enabled: true,
    reason: null,
  },
  secondary: [
    {
      label: "Проверить репозиторий",
      cli: "factory product validate --id prd_demo_001",
      api: "POST /products/prd_demo_001/validate",
      enabled: true,
      reason: null,
    },
  ],
  blockers: [],
  after: "После создания задачи: агент сформулирует бриф, затем фаза «Требования».",
};

export const productGuidanceCreated: Guidance = {
  schema_version: 1,
  subject: { kind: "product", id: "prd_demo_002" },
  phase: null,
  headline: "Продукт зарегистрирован, репозиторий ещё не проверен",
  why: "Фабрика не подтверждала доступ к репозиторию: готовность не наблюдалась.",
  primary: {
    label: "Проверить репозиторий",
    cli: "factory product validate --id prd_demo_002",
    api: "POST /products/prd_demo_002/validate",
    enabled: true,
    reason: null,
  },
  secondary: [
    {
      label: "Показать продукт",
      cli: "factory product show --id prd_demo_002",
      api: "GET /products/prd_demo_002",
      enabled: true,
      reason: null,
    },
  ],
  blockers: [
    {
      what: "Репозиторий не проверен",
      who: "operator",
      how: "Запустите проверку: фабрика попробует дойти до репозитория.",
    },
  ],
  after: "После проверки: продукт станет ready, и можно создать первую задачу.",
};

/** A change whose brief is complete: the primary is a CLI-only run advance. */
export const changeGuidanceStart: Guidance = {
  schema_version: 1,
  subject: { kind: "change", id: "chg_demo_001" },
  phase: "initiative",
  headline: "Бриф готов — можно запускать фазу «Требования»",
  why: "Проблема и цель сформулированы; сценарий specs-only: задача завершится после согласования спецификаций.",
  primary: {
    label: "Запустить фазу «Требования»",
    cli: "factory run advance --change-id chg_demo_001",
    api: null,
    enabled: true,
    reason: null,
  },
  secondary: [
    { label: "Дополнить бриф", cli: null, api: "PUT /changes/chg_demo_001/brief", enabled: true, reason: null },
  ],
  blockers: [],
  after: "После запуска: агент product соберёт требования; согласование — на гейте specification.",
};

/** A draft brief: the primary is the brief editor, blocked on the operator. */
export const changeGuidanceDraft: Guidance = {
  schema_version: 1,
  subject: { kind: "change", id: "chg_demo_001" },
  phase: "initiative",
  headline: "Бриф не завершён",
  why: "Не заполнены: problem, goal.",
  primary: {
    label: "Дополнить бриф",
    cli: null,
    api: "PUT /changes/chg_demo_001/brief",
    enabled: true,
    reason: null,
  },
  secondary: [
    {
      label: "Запустить фазу «Требования»",
      cli: "factory run advance --change-id chg_demo_001",
      api: null,
      enabled: false,
      reason: "Бриф не завершён: требования нельзя начать без problem и goal.",
    },
  ],
  blockers: [
    { what: "Бриф — черновик", who: "operator", how: "Заполните problem и goal или попросите агента сформулировать." },
  ],
  after: null,
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

export function makeChangeCard(decisionsCount: number, base: Change = change): ChangeCard {
  return {
    ...base,
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
