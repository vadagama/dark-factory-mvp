/**
 * Synthetic API fixtures for unit tests (ADR-021 p.4: no secrets, no real
 * tokens — only clearly synthetic data shaped like the T035 wire contract).
 */

import type {
  ArtifactDiff,
  ArtifactDocumentView,
  ArtifactDraftView,
  ArtifactRevision,
  ArtifactTreeView,
  ArtifactWriteView,
  Change,
  ChangeCard,
  ChangeTrace,
  CiStage,
  CiStages,
  CommentView,
  Decision,
  Evidence,
  Finding,
  GateResult,
  Guidance,
  IntakeBrief,
  PhaseGate,
  Product,
  ProductValidationView,
  Question,
  ReworkOrder,
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

// -- M2: conversations, phase gate, artifacts (T086–T090, ADR-034/ADR-035) --

export const SPEC_PATH = ".factory/changes/2026/CHG-0001-health/spec/requirements/REQ-001-health.md";
export const INTENT_PATH = ".factory/changes/2026/CHG-0001-health/spec/intent.md";
export const HEAD_REVISION = "a1b2c3d4e5f6a7b8";
export const OLD_REVISION = "0011223344556677";

/** Content with constructs a renderer does not know — they must survive the mode round-trip verbatim. */
export const SPEC_CONTENT = `---
schema: sdd/requirement@1
id: REQ-001
type: requirement
title: Health endpoint
status: draft
priority: 2
---
# REQ-001 Health endpoint {#req-001}

:::note
Custom container the renderer does not know.
:::

<!-- html comment kept verbatim -->

- AC-1: GET /health answers 200 when ready.
- AC-2: GET /health answers 503 while starting.

| a | b |
|---|---|
| 1 | 2 |

Term[^1] and ~~strike~~ and ==mark==.

[^1]: footnote
`;

export const specDocument: ArtifactDocumentView = {
  path: SPEC_PATH,
  kind: "spec",
  revision: HEAD_REVISION,
  content: SPEC_CONTENT,
  properties: {
    values: {
      schema: "sdd/requirement@1",
      id: "REQ-001",
      type: "requirement",
      title: "Health endpoint",
      status: "draft",
      priority: 2,
    },
    protected: ["schema", "id", "type", "product", "change"],
  },
  body: SPEC_CONTENT.slice(SPEC_CONTENT.indexOf("# REQ-001")),
  anchors: ["REQ-001", "req-001", "req-001-health-endpoint", "AC-1", "AC-2"],
  frontmatter_error: null,
  draft: null,
  viewed: false,
  open_comments: 1,
  open_questions: 1,
};

export const intentDocument: ArtifactDocumentView = {
  path: INTENT_PATH,
  kind: "spec",
  revision: HEAD_REVISION,
  content: "# Intent\n\nLiveness probe for the demo service.\n",
  properties: null,
  body: "# Intent\n\nLiveness probe for the demo service.\n",
  anchors: ["intent"],
  frontmatter_error: null,
  draft: null,
  viewed: true,
  open_comments: 0,
  open_questions: 0,
};

export const specDraft: ArtifactDraftView = {
  change_id: "chg_demo_001",
  artifact: SPEC_PATH,
  content: SPEC_CONTENT.replace("status: draft", "status: review"),
  base_revision: HEAD_REVISION,
  saved_by: "operator@example",
  updated_at: "2026-09-05T09:30:00Z",
  stale: false,
};

export const artifactTree: ArtifactTreeView = {
  change_id: "chg_demo_001",
  branch: "change/CHG-0001-health",
  revision: HEAD_REVISION,
  nodes: [
    { path: INTENT_PATH, kind: "spec", revision: HEAD_REVISION },
    { path: SPEC_PATH, kind: "spec", revision: HEAD_REVISION },
    { path: ".factory/changes/2026/CHG-0001-health/design/decisions/ADR-001-probe.md", kind: "adr", revision: HEAD_REVISION },
  ],
  drafts: [],
};

/** No branch yet: `revision === null` (distinct from an empty branch). */
export const artifactTreeNoBranch: ArtifactTreeView = {
  change_id: "chg_demo_001",
  branch: "change/CHG-0001-health",
  revision: null,
  nodes: [],
  drafts: [],
};

export const artifactVersions: ArtifactRevision[] = [
  { revision: HEAD_REVISION, message: "spec: clarify AC-2", author: "product-agent", authored_at: "2026-09-05T09:00:00Z" },
  { revision: OLD_REVISION, message: "spec: initial requirements", author: "product-agent", authored_at: "2026-09-04T12:00:00Z" },
];

export const artifactDiff: ArtifactDiff = {
  path: SPEC_PATH,
  from_revision: OLD_REVISION,
  to_revision: HEAD_REVISION,
  unified: `--- ${SPEC_PATH}@${OLD_REVISION}\n+++ ${SPEC_PATH}@${HEAD_REVISION}\n@@ -12,1 +12,1 @@\n-- AC-2: GET /health answers 500 while starting.\n+- AC-2: GET /health answers 503 while starting.\n`,
  added: 1,
  removed: 1,
};

export const artifactWrite: ArtifactWriteView = {
  path: SPEC_PATH,
  revision: "ffee0011ffee0011",
  previous_revision: HEAD_REVISION,
  created_commit: true,
  stale_questions: [],
  detached_comments: [],
};

export const questionChoice: Question = {
  id: "q_choice_001",
  change_id: "chg_demo_001",
  phase: "requirements",
  run_id: "run_demo_001",
  asked_by: "product",
  text: "Какой код возвращать, пока сервис стартует?",
  kind: "choice",
  options: ["503", "500", "200 с телом not-ready"],
  anchor: { artifact: SPEC_PATH, anchor_id: "AC-2", revision: HEAD_REVISION },
  blocking: true,
  status: "open",
  answer: null,
  asked_at: "2026-09-05T09:05:00Z",
  updated_at: "2026-09-05T09:05:00Z",
};

export const questionText: Question = {
  ...questionChoice,
  id: "q_text_002",
  text: "Какой таймаут проверки зависимостей допустим?",
  kind: "number",
  options: [],
  anchor: { artifact: SPEC_PATH, anchor_id: "AC-1", revision: HEAD_REVISION },
};

/** A non-blocking question is rendered as an assumption card («Подтвердить / Исправить / Варианты»). */
export const assumptionText: Question = {
  ...questionChoice,
  id: "q_assume_003",
  text: "Полагаю, что /health не требует аутентификации.",
  kind: "text",
  options: [],
  anchor: { artifact: SPEC_PATH, anchor_id: "REQ-001", revision: HEAD_REVISION },
  blocking: false,
};

export const assumptionChoice: Question = {
  ...questionChoice,
  id: "q_assume_004",
  text: "Полагаю, что формат ответа — JSON.",
  kind: "choice",
  options: ["JSON", "text/plain"],
  blocking: false,
};

export const questionAnswered: Question = {
  ...questionText,
  id: "q_answered_005",
  text: "Нужен ли readiness отдельно от liveness?",
  kind: "text",
  status: "answered",
  answer: { value: "нет, только liveness", comment: null, answered_by: "operator@example", answered_at: "2026-09-05T09:20:00Z" },
};

export const commentOpen: CommentView = {
  id: "cmt_open_001",
  change_id: "chg_demo_001",
  phase: "requirements",
  anchor: { artifact: SPEC_PATH, anchor_id: "AC-1", revision: HEAD_REVISION },
  body: "Уточнить, что «готовность» включает соединение с БД.",
  author: "operator@example",
  status: "open",
  rework_order_id: null,
  addressed_note: null,
  created_at: "2026-09-05T09:10:00Z",
  updated_at: "2026-09-05T09:10:00Z",
  anchor_state: "attached",
};

/** The fragment disappeared after an edit: shown explicitly, never re-attached (ADR-034 p.1). */
export const commentDetached: CommentView = {
  ...commentOpen,
  id: "cmt_detached_002",
  anchor: { artifact: SPEC_PATH, anchor_id: "AC-3", revision: OLD_REVISION },
  body: "AC-3 дублирует AC-1.",
  anchor_state: "detached",
};

export const commentAddressed: CommentView = {
  ...commentOpen,
  id: "cmt_addressed_003",
  anchor: { artifact: SPEC_PATH, anchor_id: "AC-2", revision: OLD_REVISION },
  body: "503 вместо 500 при старте.",
  status: "addressed",
  rework_order_id: "rw_done_001",
  addressed_note: "Заменил код на 503 в AC-2.",
};

export const reworkOrderDone: ReworkOrder = {
  id: "rw_done_001",
  change_id: "chg_demo_001",
  phase: "requirements",
  revisions: { [SPEC_PATH]: OLD_REVISION },
  comment_ids: ["cmt_addressed_003"],
  question_ids: [],
  instruction: "Привести коды ответов к RFC.",
  issued_by: "operator@example",
  status: "done",
  round: 1,
  run_id: "run_demo_001",
  summary: {
    changed: ["AC-2: код при старте 500 → 503"],
    remaining: ["Таймаут проверки зависимостей не задан"],
    addressed_comment_ids: ["cmt_addressed_003"],
  },
  escalation_reason: null,
  created_at: "2026-09-04T13:00:00Z",
  updated_at: "2026-09-05T09:00:00Z",
};

export const reworkOrderPending: ReworkOrder = {
  ...reworkOrderDone,
  id: "rw_pending_002",
  comment_ids: ["cmt_open_001"],
  question_ids: ["q_answered_005"],
  instruction: "Учесть соединение с БД в готовности.",
  status: "pending",
  round: null,
  run_id: null,
  summary: null,
  created_at: "2026-09-05T10:00:00Z",
  updated_at: "2026-09-05T10:00:00Z",
};

/** The requirements gate is closed by a blocking question. */
export const phaseGateClosed: PhaseGate = {
  change_id: "chg_demo_001",
  phase: "requirements",
  gate: "specification",
  available: false,
  reasons: [
    {
      what: "Блокирующих вопросов без ответа: 2 (q_choice_001, q_text_002)",
      how: "Ответьте: factory change answer --id chg_demo_001 --question <id> --value … или POST /changes/chg_demo_001/questions/{id}/answer.",
    },
  ],
  current_revision: HEAD_REVISION,
  approved: false,
  skippable: true,
  approvals: [
    { decision_id: "dec_stale_001", outcome: "approved", revision: OLD_REVISION, state: "stale", comment: "первая версия" },
  ],
  blocking_questions: 2,
  open_questions: 4,
  answered_questions: 1,
  open_comments: 2,
  addressed_comments: 1,
  detached_comments: 1,
  rework_pending: false,
  rework_in_progress: false,
  rework_rounds_used: 1,
  rework_rounds_max: 3,
};

export const phaseGateOpen: PhaseGate = {
  ...phaseGateClosed,
  available: true,
  reasons: [],
  blocking_questions: 0,
  open_questions: 2,
  answered_questions: 3,
};

/** A phase without a gate (F0) — the server explains itself. */
export function phaseGateOf(phase: PhaseGate["phase"]): PhaseGate {
  if (phase === "requirements") {
    return phaseGateClosed;
  }
  return {
    ...phaseGateClosed,
    phase,
    gate: phase === "initiative" ? null : "planning",
    available: false,
    reasons: [{ what: `Фаза «${phase}» не имеет гейта согласования`, how: "Перейдите к следующей фазе; решение здесь не требуется." }],
    approvals: [],
    blocking_questions: 0,
    open_questions: 0,
    answered_questions: 0,
    open_comments: 0,
    addressed_comments: 0,
    detached_comments: 0,
    rework_rounds_used: 0,
  };
}

/** Requirements phase with the gate closed: the primary is «Ответить на вопросы». */
export const changeGuidanceRequirementsClosed: Guidance = {
  schema_version: 1,
  subject: { kind: "change", id: "chg_demo_001" },
  phase: "requirements",
  headline: "Фаза «Требования»: нужно решение, гейт пока закрыт",
  why: "Блокирующих вопросов без ответа: 2; вопросов без ответа: 4 (блокирующих 2); открытых замечаний: 2.",
  primary: {
    label: "Ответить на вопросы",
    cli: "factory change status --id chg_demo_001",
    api: "GET /changes/chg_demo_001/questions?status=open",
    enabled: true,
    reason: null,
  },
  secondary: [
    { label: "На доработку", cli: "factory change rework --id chg_demo_001 --phase requirements …", api: "POST /changes/chg_demo_001/rework-orders", enabled: true, reason: null },
    { label: "Посмотреть артефакты", cli: "factory change artifacts --id chg_demo_001 list", api: "GET /changes/chg_demo_001/artifacts", enabled: true, reason: null },
  ],
  blockers: [
    { what: "Блокирующих вопросов без ответа: 2 (q_choice_001, q_text_002)", who: "operator", how: "Ответьте на вопросы агента." },
  ],
  after: "После ответов: гейт откроется, можно согласовать требования.",
};

/** Requirements phase with the gate open: the primary is the approval. */
export const changeGuidanceRequirementsOpen: Guidance = {
  ...changeGuidanceRequirementsClosed,
  headline: "Фаза «Требования» готова к согласованию",
  why: "Агент подготовил результат; ожидается решение по гейту specification.",
  primary: {
    label: "Согласовать требования",
    cli: "factory change approve --id chg_demo_001 --phase requirements",
    api: "POST /changes/chg_demo_001/approvals",
    enabled: true,
    reason: null,
  },
  blockers: [],
  after: "После согласования: фаза «Архитектура».",
};
