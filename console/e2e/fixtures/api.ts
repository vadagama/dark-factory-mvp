/**
 * Synthetic API fixtures for the Playwright smoke suite (ADR-021 p.4):
 * clearly fake data shaped like the T035 wire contract, served by
 * `page.route` — no real API and no secrets are involved in e2e runs.
 */

import type { Page, Route } from "@playwright/test";
import type {
  ArtifactDocumentView,
  ArtifactDraftView,
  ArtifactRevision,
  ArtifactTreeView,
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
  Phase,
  PhaseGate,
  Product,
  ProductValidationView,
  Question,
  ReworkOrder,
  RunCard,
  RunSummary,
} from "../../src/api/types";

// Products (T065/T066, ADR-030): one ready product with a change, one that was
// registered but never validated — so both guidance branches are exercised.
const product1: Product = {
  id: "prd_demo_001",
  name: "Demo service",
  description: "Synthetic demo product",
  repository: { provider: "github", slug: "acme/demo-service" },
  repository_url: "https://github.com/acme/demo-service",
  baseline_ref: "baseline/2026-09",
  dev_env_ref: null,
  status: "ready",
  status_reason: null,
  state_revision: 3,
  created_at: "2026-08-30T09:00:00Z",
};

const product2: Product = {
  id: "prd_demo_002",
  name: "Legacy API",
  description: null,
  repository: { provider: "gitlab", slug: "acme/legacy-api" },
  repository_url: null,
  baseline_ref: null,
  dev_env_ref: null,
  status: "created",
  status_reason: null,
  state_revision: 1,
  created_at: "2026-09-02T08:00:00Z",
};

const product2Validated: ProductValidationView = {
  ...product2,
  status: "ready",
  state_revision: 2,
  validation: {
    repository: product2.repository,
    state: "baseline_absent",
    default_branch: "main",
    head_revision: "0123abcd4567",
  },
};

const briefComplete: IntakeBrief = {
  problem: "Нет проверки живости сервиса",
  goal: "Эндпоинт /health отвечает 200 при готовности",
  constraints: ["Без новых зависимостей"],
  out_of_scope: ["Метрики readiness"],
  source_text: "Нужен health endpoint для демо-сервиса",
  status: "complete",
  formulated_by: "agent",
  error: null,
};

/** Formulate answers a draft with the cause when the source text asks for it (T072 DoD). */
const FORMULATE_FAIL_MARKER = "[harness-down]";

function briefDraft(sourceText: string): IntakeBrief {
  return {
    problem: null,
    goal: null,
    constraints: [],
    out_of_scope: [],
    source_text: sourceText,
    status: "draft",
    formulated_by: null,
    error: "Agent harness is not configured on this contour",
  };
}

const productGuidance1: Guidance = {
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
      label: "Повторить проверку",
      cli: "factory product validate --id prd_demo_001",
      api: "POST /products/prd_demo_001/validate",
      enabled: true,
      reason: null,
    },
  ],
  blockers: [],
  after: "Задача получит бриф, сценарий и лимит расхода; следующий шаг — фаза «Требования».",
};

const productGuidance2: Guidance = {
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
  secondary: [],
  blockers: [
    {
      what: "Репозиторий не проверен",
      who: "operator",
      how: "Запустите проверку: фабрика попробует дойти до репозитория.",
    },
  ],
  after: "После проверки: продукт станет ready, и можно создать первую задачу.",
};

/** Guidance of a freshly created change: the primary is CLI-only (run advance comes to the Console in M4). */
function newChangeGuidance(change: Change): Guidance {
  const complete = change.brief?.status === "complete";
  return {
    schema_version: 1,
    subject: { kind: "change", id: change.id },
    phase: "initiative",
    headline: complete ? "Бриф готов — можно запускать фазу «Требования»" : "Бриф не завершён",
    why: complete ? "Проблема и цель сформулированы." : "Не заполнены: problem, goal.",
    primary: complete
      ? {
          label: "Запустить фазу «Требования»",
          cli: `factory run advance --change-id ${change.id}`,
          api: null,
          enabled: true,
          reason: null,
        }
      : { label: "Дополнить бриф", cli: null, api: `PUT /changes/${change.id}/brief`, enabled: true, reason: null },
    secondary: [],
    blockers: complete
      ? []
      : [{ what: "Бриф — черновик", who: "operator", how: "Заполните problem и goal." }],
    after: null,
  };
}

const changeGuidance1: Guidance = {
  schema_version: 1,
  subject: { kind: "change", id: "chg_demo_001" },
  phase: "done",
  headline: "Задача завершена",
  why: "Все стадии завершены.",
  primary: {
    label: "Открыть результат",
    cli: "factory change status --id chg_demo_001",
    api: "GET /changes/chg_demo_001",
    enabled: true,
    reason: null,
  },
  secondary: [],
  blockers: [],
  after: null,
};

const changeGuidance2: Guidance = {
  schema_version: 1,
  subject: { kind: "change", id: "chg_demo_002" },
  phase: "plan",
  headline: "Фаза «План» выполняется",
  why: "Агент planning работает; вмешательство не требуется.",
  primary: {
    label: "Показать состояние",
    cli: "factory run status --run-id run_demo_002",
    api: "GET /runs/run_demo_002",
    enabled: true,
    reason: null,
  },
  secondary: [
    { label: "Снять запуск", cli: "factory run withdraw --run-id run_demo_002", api: "POST /runs/run_demo_002/withdraw", enabled: true, reason: null },
  ],
  blockers: [],
  after: "После плана: исполнение.",
};

const change1: Change = {
  id: "chg_demo_001",
  title: "Add /health endpoint",
  description: "Liveness probe for the demo service",
  source: "console",
  external_ref: null,
  product: { provider: "github", slug: "acme/demo-service" },
  risk_class: "R2",
  change_request: null,
  created_at: "2026-09-01T10:00:00Z",
  product_id: "prd_demo_001",
  brief: briefComplete,
  scenario: "full",
  spend_limit: { cost_budget_usd: "25.0000", token_budget: null },
};

const change2: Change = {
  id: "chg_demo_002",
  title: "Split legacy billing module",
  description: null,
  source: "cli",
  external_ref: "ACME-4242",
  product: { provider: "gitlab", slug: "acme/legacy-api" },
  risk_class: "R3",
  change_request: null,
  created_at: "2026-09-01T09:00:00Z",
  product_id: null,
  brief: null,
  scenario: "full",
  spend_limit: null,
};

const run1Summary: RunSummary = {
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

const run2Summary: RunSummary = {
  run_id: "run_demo_002",
  change_id: "chg_demo_002",
  route: "standard",
  provider: "gitlab",
  status: "running",
  state_revision: 1,
  created_at: "2026-09-01T10:20:00Z",
  updated_at: "2026-09-01T10:25:00Z",
  finished_at: null,
};

const run1Card: RunCard = {
  ...run1Summary,
  stages: [
    { stage: "specification", status: "succeeded", input_revision: null, attempt_count: 1 },
    { stage: "planning", status: "succeeded", input_revision: "abc1234", attempt_count: 1 },
    { stage: "construction", status: "succeeded", input_revision: "abc1234", attempt_count: 2 },
    { stage: "review_verification", status: "succeeded", input_revision: "def5678", attempt_count: 1 },
    { stage: "release", status: "succeeded", input_revision: "def5678", attempt_count: 1 },
  ],
  usage: { prompt_tokens: 12500, completion_tokens: 4300, cost: "0.2847", manual_interventions: 1 },
  gates: [
    { gate: "review", status: "passed", sha: "abc1234def", summary: "2 approvals", evidence_ids: ["ev_001"] },
  ],
  open_blockers: 1,
};

const run2Card: RunCard = {
  ...run2Summary,
  stages: [
    { stage: "specification", status: "succeeded", input_revision: null, attempt_count: 1 },
    { stage: "planning", status: "in_progress", input_revision: "abc1234", attempt_count: 1 },
  ],
  usage: { prompt_tokens: 900, completion_tokens: 100, cost: null, manual_interventions: 0 },
  gates: [
    { gate: "specification", status: "passed", sha: null, summary: null, evidence_ids: [] },
  ],
  open_blockers: 0,
};

const card1: ChangeCard = {
  ...change1,
  runs: [{ run_id: "run_demo_001", status: "succeeded" }],
  decisions_count: 1,
};

const card2: ChangeCard = {
  ...change2,
  runs: [{ run_id: "run_demo_002", status: "running" }],
  decisions_count: 0,
};

const trace1: ChangeTrace = {
  change_id: "chg_demo_001",
  runs: [
    {
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
    },
  ],
};

const trace2: ChangeTrace = {
  change_id: "chg_demo_002",
  runs: [
    {
      change_id: "chg_demo_002",
      run_id: "run_demo_002",
      status: "running",
      chain: [
        { stage: "specification", status: "succeeded", input_revision: null, attempt_number: 1, produced_at: "2026-09-01T10:22:00Z", artifacts: [] },
        { stage: "planning", status: "in_progress", input_revision: "abc1234", attempt_number: 1, produced_at: "2026-09-01T10:25:00Z", artifacts: [] },
      ],
    },
  ],
};

const approval1: Decision = {
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

/** Response for a recorded approval in the smoke suite. */
const newApproval: Decision = {
  id: "dec_new001",
  gate: "review",
  outcome: "approved",
  decided_by: "human",
  role: null,
  decided_at: "2026-09-01T12:00:00Z",
  commit_sha: "deadbeef1234",
  comment: null,
  evidence_ids: [],
};

const gate1: GateResult[] = [
  { gate: "review", status: "passed", sha: "abc1234def", summary: "2 approvals", evidence_ids: ["ev_001"] },
];

const gate2: GateResult[] = [
  { gate: "specification", status: "passed", sha: null, summary: null, evidence_ids: [] },
];

const blocker1: Finding = {
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

const evidence1: Evidence = {
  id: "ev_001",
  type: "test_results",
  uri: "https://ci.example.com/jobs/1",
  checksum: null,
  produced_at: "2026-09-01T10:14:00Z",
  required: true,
  available: true,
};

// CI stage toggles (T059/ADR-026); one stage is off so the bulk action and the
// "выключен" state are exercised by the smoke suite.
const ciStageFixtures: CiStage[] = [
  {
    job: "lint",
    title: "Ruff lint + format",
    group: "python",
    summary: "Стиль, импорты и форматирование (ruff check и ruff format --check).",
    local_command: "uv run ruff check . && uv run ruff format --check .",
    weight: "light",
    variable: "CI_SKIP_LINT",
    enabled: true,
  },
  {
    job: "factory-us1-parity",
    title: "Factory US1 parity",
    group: "factory",
    summary: "factory doctor и stage run по фикстуре — walking skeleton без LLM.",
    local_command: "uv run factory doctor --json",
    weight: "medium",
    variable: "CI_SKIP_FACTORY_US1_PARITY",
    enabled: true,
  },
  {
    job: "console-e2e",
    title: "Playwright smoke",
    group: "console",
    summary: "Сборка, vite preview и smoke-сценарии с установкой Chromium.",
    local_command: "cd console && npx playwright install chromium && npm run e2e",
    weight: "heavy",
    variable: "CI_SKIP_CONSOLE_E2E",
    enabled: false,
  },
  {
    job: "console-image",
    title: "Console image",
    group: "image",
    summary: "gitleaks, сборка бандла, multi-arch образ консоли, trivy и SBOM.",
    local_command: "cd console && npm ci && npm run build && docker build -t dark-factory-console:local .",
    weight: "heavy",
    variable: "CI_SKIP_CONSOLE_IMAGE",
    enabled: true,
  },
];

const ciStagesFixture: CiStages = {
  schema_version: 1,
  repository: "vadagama/dark-factory-mvp",
  available: true,
  reason: null,
  stages: ciStageFixtures,
};

// --- M2: a change in the requirements phase (T088–T090, ADR-034/ADR-035) --------
// chg_demo_003 has a branch with two spec artifacts, one blocking choice
// question, one assumption; the stub keeps state per scenario so the cycle
// «вопрос → ответ → правка → сводка → согласовать / на доработку» runs end to end.

const change3: Change = {
  id: "chg_demo_003",
  title: "Add rate limiting",
  description: "Protect the public API from bursts",
  source: "console",
  external_ref: null,
  product: { provider: "gitlab", slug: "acme/legacy-api" },
  risk_class: "R2",
  change_request: null,
  created_at: "2026-09-04T09:00:00Z",
  product_id: "prd_demo_002",
  brief: { ...briefComplete, problem: "Публичный API падает при всплесках", goal: "429 при превышении лимита", source_text: "нужен rate limiting" },
  scenario: "specs_only",
  spend_limit: { cost_budget_usd: "40.0000", token_budget: null },
};

const run3Summary: RunSummary = {
  run_id: "run_demo_003",
  change_id: "chg_demo_003",
  route: "standard",
  provider: "gitlab",
  status: "waiting",
  state_revision: 1,
  created_at: "2026-09-04T09:01:00Z",
  updated_at: "2026-09-05T09:00:00Z",
  finished_at: null,
};

const run3Card: RunCard = {
  ...run3Summary,
  stages: [{ stage: "specification", status: "waiting", input_revision: null, attempt_count: 1 }],
  usage: { prompt_tokens: 3200, completion_tokens: 900, cost: "0.1200", manual_interventions: 0 },
  gates: [{ gate: "specification", status: "pending", sha: null, summary: null, evidence_ids: [] }],
  open_blockers: 0,
};

const card3: ChangeCard = { ...change3, runs: [{ run_id: "run_demo_003", status: "waiting" }], decisions_count: 0 };
const trace3: ChangeTrace = { change_id: "chg_demo_003", runs: [] };

export const E2E_SPEC_PATH = ".factory/changes/2026/CHG-0003-rate-limit/spec/requirements/REQ-001-rate-limit.md";
export const E2E_INTENT_PATH = ".factory/changes/2026/CHG-0003-rate-limit/spec/intent.md";
export const E2E_HEAD = "e2e0001e2e0001e2e0001";

export const E2E_SPEC_CONTENT = `---
schema: sdd/requirement@1
id: REQ-001
type: requirement
title: Rate limiting
status: draft
---
# REQ-001 Rate limiting {#req-001}

:::note
Construct unknown to the renderer — kept verbatim.
:::

- AC-1: the API answers 429 above 100 requests per minute per client.
- AC-2: the limit is configurable without a redeploy.
`;

const E2E_INTENT_CONTENT = "# Intent\n\nProtect the public API from bursts.\n";

/** Anchors the way the server computes them (frontmatter id, heading slug, stable ids). */
function anchorsOf(content: string): string[] {
  const found = new Set<string>();
  const idMatch = content.match(/^id:\s*(\S+)$/m);
  if (idMatch) {
    found.add(idMatch[1]);
  }
  for (const match of content.matchAll(/\{#([A-Za-z0-9_.:-]+)\}/g)) {
    found.add(match[1]);
  }
  for (const match of content.matchAll(/\b(?:REQ|AC|ADR)-[0-9]+\b/g)) {
    found.add(match[0]);
  }
  return [...found];
}

function propertiesOf(content: string): ArtifactDocumentView["properties"] {
  if (!content.startsWith("---\n")) {
    return null;
  }
  const end = content.indexOf("\n---\n", 4);
  const block = content.slice(4, end);
  const values: Record<string, unknown> = {};
  for (const line of block.split("\n")) {
    const separator = line.indexOf(":");
    if (separator > 0) {
      values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
    }
  }
  return { values, protected: ["schema", "id", "type", "product", "change"] };
}

function bodyOfContent(content: string): string {
  if (!content.startsWith("---\n")) {
    return content;
  }
  const end = content.indexOf("\n---\n", 4);
  return end === -1 ? content : content.slice(end + 5);
}

/** Naive unified diff: enough for the smoke suite to render added/removed lines. */
function unifiedDiff(path: string, from: string, to: string, before: string, after: string): string {
  const beforeLines = before.split("\n");
  const afterLines = after.split("\n");
  const removed = beforeLines.filter((line) => !afterLines.includes(line)).map((line) => `-${line}`);
  const added = afterLines.filter((line) => !beforeLines.includes(line)).map((line) => `+${line}`);
  if (removed.length === 0 && added.length === 0) {
    return "";
  }
  return [`--- ${path}@${from}`, `+++ ${path}@${to}`, "@@ @@", ...removed, ...added, ""].join("\n");
}

const questionsSeed: Question[] = [
  {
    id: "q_e2e_choice",
    change_id: "chg_demo_003",
    phase: "requirements",
    run_id: "run_demo_003",
    asked_by: "product",
    text: "Какой код возвращать при превышении лимита?",
    kind: "choice",
    options: ["429", "503"],
    anchor: { artifact: E2E_SPEC_PATH, anchor_id: "AC-1", revision: E2E_HEAD },
    blocking: true,
    status: "open",
    answer: null,
    asked_at: "2026-09-05T09:05:00Z",
    updated_at: "2026-09-05T09:05:00Z",
  },
  {
    id: "q_e2e_assume",
    change_id: "chg_demo_003",
    phase: "requirements",
    run_id: "run_demo_003",
    asked_by: "product",
    text: "Полагаю, что лимит считается на клиента, а не на IP.",
    kind: "text",
    options: [],
    anchor: { artifact: E2E_SPEC_PATH, anchor_id: "REQ-001", revision: E2E_HEAD },
    blocking: false,
    status: "open",
    answer: null,
    asked_at: "2026-09-05T09:06:00Z",
    updated_at: "2026-09-05T09:06:00Z",
  },
];

/** Per-scenario mutable state of chg_demo_003: git-like documents, drafts, discussion. */
interface M2State {
  head: string;
  counter: number;
  contents: Map<string, string>;
  history: Map<string, ArtifactRevision[]>;
  snapshots: Map<string, string>;
  drafts: Map<string, ArtifactDraftView>;
  viewed: Set<string>;
  questions: Question[];
  comments: CommentView[];
  orders: ReworkOrder[];
  decisions: Decision[];
}

function newM2State(): M2State {
  const contents = new Map<string, string>([
    [E2E_INTENT_PATH, E2E_INTENT_CONTENT],
    [E2E_SPEC_PATH, E2E_SPEC_CONTENT],
  ]);
  const first: ArtifactRevision = { revision: E2E_HEAD, message: "spec: initial requirements", author: "product-agent", authored_at: "2026-09-05T09:00:00Z" };
  return {
    head: E2E_HEAD,
    counter: 1,
    contents,
    history: new Map([
      [E2E_INTENT_PATH, [first]],
      [E2E_SPEC_PATH, [first]],
    ]),
    snapshots: new Map([[`${E2E_SPEC_PATH}@${E2E_HEAD}`, E2E_SPEC_CONTENT], [`${E2E_INTENT_PATH}@${E2E_HEAD}`, E2E_INTENT_CONTENT]]),
    drafts: new Map(),
    viewed: new Set(),
    questions: questionsSeed.map((question) => ({ ...question })),
    comments: [],
    orders: [],
    decisions: [],
  };
}

function m2PhaseGate(state: M2State, changeId: string, phase: Phase): PhaseGate {
  const own = changeId === "chg_demo_003" && phase === "requirements";
  const blocking = own ? state.questions.filter((q) => q.blocking && q.status === "open").length : 0;
  const pending = own && state.orders.some((order) => order.status === "pending");
  const reasons: PhaseGate["reasons"] = [];
  if (phase === "initiative") {
    reasons.push({ what: "Фаза «initiative» не имеет гейта согласования", how: "Перейдите к следующей фазе; решение здесь не требуется." });
  } else if (!own) {
    reasons.push({ what: "Артефактов фазы ещё нет: агент не создал ревизию", how: `Запустите фазу: factory run advance --change-id ${changeId}.` });
  }
  if (blocking > 0) {
    reasons.push({ what: `Блокирующих вопросов без ответа: ${blocking} (q_e2e_choice)`, how: `Ответьте: POST /changes/${changeId}/questions/{id}/answer.` });
  }
  if (pending) {
    reasons.push({ what: "Отправлено на доработку: раунд ещё не запущен", how: `Запустите раунд: factory run advance --change-id ${changeId}.` });
  }
  const approvals = own
    ? state.decisions
        .filter((decision) => decision.gate === "specification")
        .map((decision) => ({
          decision_id: decision.id,
          outcome: decision.outcome,
          revision: decision.commit_sha,
          state: (decision.commit_sha === state.head ? "current" : "stale") as "current" | "stale",
          comment: decision.comment,
        }))
    : [];
  return {
    change_id: changeId,
    phase,
    gate: phase === "initiative" ? null : phase === "requirements" ? "specification" : "planning",
    available: reasons.length === 0,
    reasons,
    current_revision: own ? state.head : null,
    approved: approvals.some((approval) => approval.outcome === "approved" && approval.state === "current"),
    skippable: phase !== "initiative",
    approvals,
    blocking_questions: blocking,
    open_questions: own ? state.questions.filter((q) => q.status === "open").length : 0,
    answered_questions: own ? state.questions.filter((q) => q.status === "answered").length : 0,
    open_comments: own ? state.comments.filter((c) => c.status === "open").length : 0,
    addressed_comments: own ? state.comments.filter((c) => c.status === "addressed").length : 0,
    detached_comments: own ? state.comments.filter((c) => c.anchor_state === "detached" && c.status !== "closed").length : 0,
    rework_pending: pending,
    rework_in_progress: false,
    rework_rounds_used: own ? state.orders.filter((order) => order.status !== "pending").length : 0,
    rework_rounds_max: 3,
  };
}

function m2Guidance(state: M2State): Guidance {
  const gate = m2PhaseGate(state, "chg_demo_003", "requirements");
  const approve = { label: "Согласовать требования", cli: "factory change approve --id chg_demo_003 --phase requirements", api: "POST /changes/chg_demo_003/approvals", enabled: true, reason: null };
  const rework = { label: "На доработку", cli: "factory change rework --id chg_demo_003 --phase requirements …", api: "POST /changes/chg_demo_003/rework-orders", enabled: true, reason: null };
  const artifacts = { label: "Посмотреть артефакты", cli: "factory change artifacts --id chg_demo_003 list", api: "GET /changes/chg_demo_003/artifacts", enabled: true, reason: null };
  const questions = { label: "Ответить на вопросы", cli: "factory change status --id chg_demo_003", api: "GET /changes/chg_demo_003/questions?status=open", enabled: true, reason: null };
  const counts = `вопросов без ответа: ${gate.open_questions} (блокирующих ${gate.blocking_questions}); открытых замечаний: ${gate.open_comments}`;
  if (gate.approved) {
    return {
      schema_version: 1,
      subject: { kind: "change", id: "chg_demo_003" },
      phase: "requirements",
      headline: "Фаза «Требования» согласована на текущей ревизии",
      why: `Решение записано на ревизии ${state.head}; ${counts}.`,
      primary: { label: "Продолжить после согласования", cli: "factory run advance --run-id run_demo_003", api: null, enabled: true, reason: null },
      secondary: [rework, artifacts],
      blockers: [],
      after: "Следующая фаза после «Требования».",
    };
  }
  if (!gate.available) {
    const first = gate.reasons[0];
    return {
      schema_version: 1,
      subject: { kind: "change", id: "chg_demo_003" },
      phase: "requirements",
      headline: "Фаза «Требования»: нужно решение, гейт пока закрыт",
      why: `${first.what}; ${counts}.`,
      primary: gate.blocking_questions > 0 ? questions : { ...approve, enabled: false, reason: first.what },
      secondary: [rework, artifacts],
      blockers: gate.reasons.map((reason) => ({ what: reason.what, who: "operator" as const, how: reason.how })),
      after: "После ответов: гейт откроется, можно согласовать требования.",
    };
  }
  return {
    schema_version: 1,
    subject: { kind: "change", id: "chg_demo_003" },
    phase: "requirements",
    headline: "Фаза «Требования» готова к согласованию",
    why: `Агент подготовил результат; ${counts}.`,
    primary: approve,
    secondary: [rework, artifacts],
    blockers: [],
    after: "После согласования: фаза «Архитектура».",
  };
}

function m2Document(state: M2State, path: string, revision: string | null): ArtifactDocumentView | null {
  const content = revision ? state.snapshots.get(`${path}@${revision}`) : state.contents.get(path);
  if (content === undefined) {
    return null;
  }
  const rev = revision ?? state.head;
  const draft = state.drafts.get(path) ?? null;
  return {
    path,
    kind: "spec",
    revision: rev,
    content,
    properties: propertiesOf(content),
    body: bodyOfContent(content),
    anchors: anchorsOf(content),
    frontmatter_error: null,
    draft: draft ? { ...draft, stale: draft.base_revision !== state.head } : null,
    viewed: state.viewed.has(`${path}@${rev}`),
    open_comments: state.comments.filter((c) => c.anchor.artifact === path && c.status !== "closed").length,
    open_questions: state.questions.filter((q) => q.anchor?.artifact === path && q.status === "open").length,
  };
}

function m2Tree(state: M2State): ArtifactTreeView {
  return {
    change_id: "chg_demo_003",
    branch: "change/CHG-0003-rate-limit",
    revision: state.head,
    nodes: [...state.contents.keys()].map((path) => ({ path, kind: "spec" as const, revision: state.head })),
    drafts: [...state.drafts.keys()],
  };
}

const ARTIFACT_ROUTE = /^\/api\/v1\/changes\/([^/]+)\/(artifacts|artifact-versions|artifact-diff|artifact-drafts|artifact-views)\/(.+)$/;

function problem(status: number, title: string, detail: string): object {
  return { type: "about:blank", title, status, detail };
}

/** Recorded POST requests, for assertions in the specs. */
export interface CapturedPost {
  pathname: string;
  body: unknown;
  headers: Record<string, string>;
}

/** Recorded write requests (POST/PUT), for assertions in the specs. */
export interface CapturedWrite extends CapturedPost {
  method: string;
}

// Routes every request under the api/v1 path prefix to the synthetic
// dataset. Returns the captured POSTs and all writes (POST/PUT) made by the
// app under test.
export async function stubApi(
  page: Page,
): Promise<{ posts: CapturedPost[]; writes: CapturedWrite[] }> {
  const posts: CapturedPost[] = [];
  const writes: CapturedWrite[] = [];
  // Changes and products created during a scenario are served back on GET, so the
  // intake → change page flow works end to end against the stub.
  const createdChanges = new Map<string, Change>();
  const createdProducts = new Map<string, Product>();
  const m2 = newM2State();
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const { pathname, searchParams } = new URL(request.url());
    const method = request.method();

    if (method === "POST" || method === "PUT" || method === "DELETE") {
      const capture = {
        method,
        pathname,
        body: method === "DELETE" ? null : request.postDataJSON(),
        headers: request.headers(),
      };
      writes.push(capture);
      if (method === "POST") {
        posts.push(capture);
      }
    }

    // M2 endpoints (conversations, artifacts, phase gate) are stateful per scenario.
    if (await handleM2(m2, route, method, pathname, searchParams)) {
      return;
    }

    if (method === "POST") {
      if (pathname === "/api/v1/changes") {
        const created = request.postDataJSON() as Change;
        createdChanges.set(created.id, created);
        await route.fulfill({ status: 201, json: created });
        return;
      }
      if (pathname === "/api/v1/products") {
        const body = request.postDataJSON() as Product;
        const created: Product = {
          ...body,
          description: body.description ?? null,
          repository_url: body.repository_url ?? null,
          baseline_ref: body.baseline_ref ?? null,
          dev_env_ref: body.dev_env_ref ?? null,
          status: "created",
          status_reason: null,
          state_revision: 1,
          created_at: "2026-09-03T10:00:00Z",
        };
        createdProducts.set(created.id, created);
        await route.fulfill({ status: 201, json: created });
        return;
      }
      if (pathname === "/api/v1/products/prd_demo_002/validate") {
        await route.fulfill({ status: 200, json: product2Validated });
        return;
      }
      if (pathname === "/api/v1/products/prd_demo_001/validate") {
        // Provisioning is not configured for this one: the 503 detail must reach the operator.
        await route.fulfill({
          status: 503,
          json: problem(503, "Service Unavailable", "Repository provisioning is not configured on this contour"),
        });
        return;
      }
      if (pathname === "/api/v1/briefs/formulate") {
        const body = request.postDataJSON() as { source_text: string };
        const brief = body.source_text.includes(FORMULATE_FAIL_MARKER)
          ? briefDraft(body.source_text)
          : { ...briefComplete, source_text: body.source_text };
        await route.fulfill({ status: 200, json: brief });
        return;
      }
      if (pathname === "/api/v1/changes/chg_demo_001/approvals") {
        await route.fulfill({ status: 201, json: newApproval });
        return;
      }
      await route.fulfill({ status: 404, json: problem(404, "Not Found", `unmatched POST ${pathname}`) });
      return;
    }

    if (method === "PUT") {
      const briefMatch = pathname.match(/^\/api\/v1\/changes\/([^/]+)\/brief$/);
      if (briefMatch) {
        const changeId = decodeURIComponent(briefMatch[1]);
        const base = createdChanges.get(changeId) ?? (changeId === "chg_demo_001" ? change1 : null);
        if (base === null) {
          await route.fulfill({ status: 404, json: problem(404, "Not Found", `unknown change ${changeId}`) });
          return;
        }
        const updated: Change = { ...base, brief: request.postDataJSON() as IntakeBrief };
        createdChanges.set(changeId, updated);
        await route.fulfill({ status: 200, json: updated });
        return;
      }
      // CI stage toggle: a target state, answered with the single updated stage.
      const prefix = "/api/v1/ci/stages/";
      if (pathname.startsWith(prefix)) {
        const job = decodeURIComponent(pathname.slice(prefix.length));
        const stage = ciStagesFixture.stages.find((candidate) => candidate.job === job);
        if (stage === undefined) {
          await route.fulfill({ status: 404, json: problem(404, "Not Found", `unknown stage ${job}`) });
          return;
        }
        const body = request.postDataJSON() as { enabled: boolean };
        await route.fulfill({ status: 200, json: { ...stage, enabled: body.enabled } });
        return;
      }
      await route.fulfill({ status: 404, json: problem(404, "Not Found", `unmatched PUT ${pathname}`) });
      return;
    }

    // Changes created in this scenario: card, guidance and trace are derived from the POST body.
    const createdMatch = pathname.match(/^\/api\/v1\/changes\/([^/]+)(\/guidance|\/trace|\/approvals)?$/);
    if (createdMatch && createdChanges.has(decodeURIComponent(createdMatch[1]))) {
      const created = createdChanges.get(decodeURIComponent(createdMatch[1]))!;
      const suffix = createdMatch[2] ?? "";
      const body: unknown =
        suffix === "/guidance"
          ? newChangeGuidance(created)
          : suffix === "/trace"
            ? ({ change_id: created.id, runs: [] } satisfies ChangeTrace)
            : suffix === "/approvals"
              ? []
              : ({ ...created, runs: [], decisions_count: 0 } satisfies ChangeCard);
      await route.fulfill({ status: 200, json: body });
      return;
    }
    const createdProductMatch = pathname.match(/^\/api\/v1\/products\/([^/]+)(\/guidance)?$/);
    if (createdProductMatch && createdProducts.has(decodeURIComponent(createdProductMatch[1]))) {
      const created = createdProducts.get(decodeURIComponent(createdProductMatch[1]))!;
      const body: unknown = createdProductMatch[2]
        ? { ...productGuidance2, subject: { kind: "product", id: created.id } }
        : created;
      await route.fulfill({ status: 200, json: body });
      return;
    }
    if (pathname === "/api/v1/changes") {
      const productId = searchParams.get("product_id");
      const all = [change1, change2, change3, ...createdChanges.values()];
      await route.fulfill({
        status: 200,
        json: productId === null ? all : all.filter((change) => change.product_id === productId),
      });
      return;
    }

    const getRoutes: Record<string, unknown> = {
      "/api/v1/products": [product1, product2, ...createdProducts.values()],
      "/api/v1/products/prd_demo_001": product1,
      "/api/v1/products/prd_demo_002": product2,
      "/api/v1/products/prd_demo_001/guidance": productGuidance1,
      "/api/v1/products/prd_demo_002/guidance": productGuidance2,
      "/api/v1/changes/chg_demo_001/guidance": changeGuidance1,
      "/api/v1/changes/chg_demo_002/guidance": changeGuidance2,
      "/api/v1/changes/chg_demo_001": card1,
      "/api/v1/changes/chg_demo_002": card2,
      "/api/v1/changes/chg_demo_001/trace": trace1,
      "/api/v1/changes/chg_demo_002/trace": trace2,
      "/api/v1/changes/chg_demo_003": card3,
      "/api/v1/changes/chg_demo_003/trace": trace3,
      "/api/v1/runs/run_demo_003": run3Card,
      "/api/v1/runs/run_demo_003/gates": run3Card.gates,
      "/api/v1/runs/run_demo_003/findings": [],
      "/api/v1/runs/run_demo_003/evidence": [],
      "/api/v1/changes/chg_demo_001/approvals": [approval1],
      "/api/v1/changes/chg_demo_002/approvals": [],
      "/api/v1/runs": [run1Summary, run2Summary, run3Summary],
      "/api/v1/runs/run_demo_001": run1Card,
      "/api/v1/runs/run_demo_002": run2Card,
      "/api/v1/runs/run_demo_001/gates": gate1,
      "/api/v1/runs/run_demo_002/gates": gate2,
      "/api/v1/runs/run_demo_001/findings": [blocker1],
      "/api/v1/runs/run_demo_002/findings": [],
      "/api/v1/runs/run_demo_001/evidence": [evidence1],
      "/api/v1/runs/run_demo_002/evidence": [],
      "/api/v1/ci/stages": ciStagesFixture,
    };
    const body = getRoutes[pathname];
    if (body === undefined) {
      await route.fulfill({ status: 404, json: problem(404, "Not Found", `unmatched GET ${pathname}`) });
      return;
    }
    await route.fulfill({ status: 200, json: body });
  });
  return { posts, writes };
}


/**
 * Serves the M2 endpoints for every change: chg_demo_003 has real state
 * (questions, comments, orders, git-like documents with drafts); the other
 * changes answer empty discussions, a gate without a revision and 503 on the
 * artifact routes — the "no repository on this contour" contour, so the
 * honest states are exercised too. Returns false when the request is not one
 * of these routes.
 */
async function handleM2(
  state: M2State,
  route: Route,
  method: string,
  pathname: string,
  searchParams: URLSearchParams,
): Promise<boolean> {
  const request = route.request();
  const noRepository = () =>
    route.fulfill({
      status: 503,
      json: problem(503, "Service Unavailable", "the product repository is not configured in this contour, so artifacts cannot be read or written; configure the DARK_FACTORY_GITHUB_* variables"),
    });

  const artifactMatch = pathname.match(ARTIFACT_ROUTE);
  if (artifactMatch) {
    const changeId = decodeURIComponent(artifactMatch[1]);
    const kind = artifactMatch[2];
    const path = artifactMatch[3].split("/").map(decodeURIComponent).join("/");
    if (changeId !== "chg_demo_003") {
      await noRepository();
      return true;
    }
    if (kind === "artifacts" && method === "GET") {
      const doc = m2Document(state, path, searchParams.get("revision"));
      if (doc === null) {
        await route.fulfill({ status: 404, json: problem(404, "Not Found", `artifact ${path} does not exist`) });
      } else {
        await route.fulfill({ status: 200, json: doc });
      }
      return true;
    }
    if (kind === "artifacts" && method === "PUT") {
      const body = request.postDataJSON() as { content: string; base_revision: string | null; message?: string | null; properties?: Record<string, string> | null };
      if (body.base_revision !== state.head) {
        await route.fulfill({ status: 409, json: problem(409, "Conflict", `${path} changed after ${body.base_revision}: head is ${state.head}; reload and edit the current revision`) });
        return true;
      }
      let content = body.content;
      for (const [key, value] of Object.entries(body.properties ?? {})) {
        content = content.replace(new RegExp(`^${key}:.*$`, "m"), `${key}: ${String(value)}`);
      }
      const previous = state.head;
      state.counter += 1;
      state.head = `e2e${String(state.counter).padStart(4, "0")}e2e${String(state.counter).padStart(4, "0")}`;
      state.contents.set(path, content);
      state.snapshots.set(`${path}@${state.head}`, content);
      state.history.set(path, [
        { revision: state.head, message: body.message ?? "console edit", author: "operator@example", authored_at: "2026-09-05T10:00:00Z" },
        ...(state.history.get(path) ?? []),
      ]);
      state.drafts.delete(path);
      const anchors = anchorsOf(content);
      const detached: string[] = [];
      for (const comment of state.comments) {
        if (comment.anchor.artifact === path && comment.anchor.anchor_id && !anchors.includes(comment.anchor.anchor_id)) {
          comment.anchor_state = "detached";
          detached.push(comment.id);
        }
      }
      await route.fulfill({
        status: 200,
        json: { path, revision: state.head, previous_revision: previous, created_commit: true, stale_questions: [], detached_comments: detached },
      });
      return true;
    }
    if (kind === "artifact-versions") {
      await route.fulfill({ status: 200, json: state.history.get(path) ?? [] });
      return true;
    }
    if (kind === "artifact-diff") {
      const from = searchParams.get("from_revision") ?? "";
      const to = searchParams.get("to_revision") ?? "";
      const before = state.snapshots.get(`${path}@${from}`) ?? "";
      const after = state.snapshots.get(`${path}@${to}`) ?? "";
      const unified = unifiedDiff(path, from, to, before, after);
      await route.fulfill({
        status: 200,
        json: {
          path,
          from_revision: from,
          to_revision: to,
          unified,
          added: unified.split("\n").filter((line) => line.startsWith("+") && !line.startsWith("+++")).length,
          removed: unified.split("\n").filter((line) => line.startsWith("-") && !line.startsWith("---")).length,
        },
      });
      return true;
    }
    if (kind === "artifact-drafts") {
      if (method === "GET") {
        const draft = state.drafts.get(path);
        if (!draft) {
          await route.fulfill({ status: 404, json: problem(404, "Not Found", `no draft of ${path}`) });
        } else {
          await route.fulfill({ status: 200, json: { ...draft, stale: draft.base_revision !== state.head } });
        }
        return true;
      }
      if (method === "PUT") {
        const body = request.postDataJSON() as { content: string; base_revision: string | null };
        const draft: ArtifactDraftView = {
          change_id: "chg_demo_003",
          artifact: path,
          content: body.content,
          base_revision: body.base_revision,
          saved_by: "operator@example",
          updated_at: "2026-09-05T10:30:00Z",
          stale: body.base_revision !== state.head,
        };
        state.drafts.set(path, draft);
        await route.fulfill({ status: 200, json: draft });
        return true;
      }
      if (method === "DELETE") {
        state.drafts.delete(path);
        await route.fulfill({ status: 204, body: "" });
        return true;
      }
    }
    if (kind === "artifact-views" && method === "POST") {
      const body = request.postDataJSON() as { revision: string };
      state.viewed.add(`${path}@${body.revision}`);
      await route.fulfill({ status: 200, json: m2Document(state, path, null) });
      return true;
    }
    return false;
  }

  const changeMatch = pathname.match(/^\/api\/v1\/changes\/([^/]+)\/(artifacts|questions|comments|rework-orders|phase-gate|guidance|approvals)(?:\/([^/]+)(?:\/([^/]+))?)?$/);
  if (!changeMatch) {
    return false;
  }
  const changeId = decodeURIComponent(changeMatch[1]);
  const resource = changeMatch[2];
  const entityId = changeMatch[3] ? decodeURIComponent(changeMatch[3]) : null;
  const action = changeMatch[4] ?? null;
  const own = changeId === "chg_demo_003";

  if (resource === "artifacts" && method === "GET" && entityId === null) {
    if (!own) {
      await noRepository();
    } else {
      await route.fulfill({ status: 200, json: m2Tree(state) });
    }
    return true;
  }
  if (resource === "phase-gate" && method === "GET") {
    const phase = (searchParams.get("phase") ?? "requirements") as Phase;
    await route.fulfill({ status: 200, json: m2PhaseGate(state, changeId, phase) });
    return true;
  }
  if (resource === "guidance" && method === "GET" && own) {
    await route.fulfill({ status: 200, json: m2Guidance(state) });
    return true;
  }
  if (resource === "approvals" && own) {
    if (method === "GET") {
      await route.fulfill({ status: 200, json: state.decisions });
      return true;
    }
    if (method === "POST") {
      const body = request.postDataJSON() as { gate: string; outcome: "approved" | "rejected" | "waived"; subject_revision: string; comment: string | null };
      const gate = m2PhaseGate(state, changeId, "requirements");
      if (body.outcome === "approved" && !gate.available) {
        await route.fulfill({ status: 409, json: problem(409, "Conflict", `the requirements gate is closed: ${gate.reasons.map((reason) => reason.what).join("; ")}`) });
        return true;
      }
      if (body.outcome === "approved" && body.subject_revision !== state.head) {
        await route.fulfill({ status: 409, json: problem(409, "Conflict", `subject_revision ${body.subject_revision} is not the current revision ${state.head}: reload and decide on what you see`) });
        return true;
      }
      const decision: Decision = {
        id: `dec_e2e_${state.decisions.length + 1}`,
        gate: body.gate as Decision["gate"],
        outcome: body.outcome,
        decided_by: "human",
        role: null,
        decided_at: "2026-09-05T11:00:00Z",
        commit_sha: body.subject_revision,
        comment: body.comment,
        evidence_ids: [],
      };
      state.decisions.push(decision);
      await route.fulfill({ status: 201, json: decision });
      return true;
    }
  }
  if (resource === "questions") {
    if (method === "GET") {
      const status = searchParams.get("status");
      await route.fulfill({ status: 200, json: own ? state.questions.filter((q) => status === null || q.status === status) : [] });
      return true;
    }
    if (method === "POST" && entityId && action === "answer") {
      const question = state.questions.find((q) => q.id === entityId);
      if (!question) {
        await route.fulfill({ status: 404, json: problem(404, "Not Found", `Question '${entityId}' does not exist`) });
        return true;
      }
      const body = request.postDataJSON() as { value: string; comment?: string | null };
      if (question.kind === "choice" && !question.options.includes(body.value)) {
        await route.fulfill({ status: 422, json: problem(422, "Unprocessable Entity", `answer '${body.value}' is not one of the options: ${question.options.join(", ")}`) });
        return true;
      }
      question.status = "answered";
      question.answer = { value: body.value, comment: body.comment ?? null, answered_by: "operator@example", answered_at: "2026-09-05T10:10:00Z" };
      await route.fulfill({ status: 200, json: question });
      return true;
    }
  }
  if (resource === "comments") {
    if (method === "GET") {
      await route.fulfill({ status: 200, json: own ? state.comments : [] });
      return true;
    }
    if (method === "POST" && entityId === null) {
      const body = request.postDataJSON() as { artifact: string; anchor_id?: string | null; revision?: string | null; body: string; phase?: Phase | null };
      const comment: CommentView = {
        id: `cmt_e2e_${state.comments.length + 1}`,
        change_id: changeId,
        phase: body.phase ?? "requirements",
        anchor: { artifact: body.artifact, anchor_id: body.anchor_id ?? null, revision: body.revision ?? state.head },
        body: body.body,
        author: "operator@example",
        status: "open",
        rework_order_id: null,
        addressed_note: null,
        created_at: "2026-09-05T10:15:00Z",
        updated_at: "2026-09-05T10:15:00Z",
        anchor_state: "attached",
      };
      state.comments.push(comment);
      await route.fulfill({ status: 201, json: comment });
      return true;
    }
    if (method === "POST" && entityId && (action === "close" || action === "reopen")) {
      const comment = state.comments.find((c) => c.id === entityId);
      if (!comment) {
        await route.fulfill({ status: 404, json: problem(404, "Not Found", `Comment '${entityId}' does not exist`) });
        return true;
      }
      comment.status = action === "close" ? "closed" : "open";
      await route.fulfill({ status: 200, json: comment });
      return true;
    }
  }
  if (resource === "rework-orders") {
    if (method === "GET") {
      await route.fulfill({ status: 200, json: own ? state.orders : [] });
      return true;
    }
    if (method === "POST") {
      const body = request.postDataJSON() as { phase?: Phase | null; comment_ids: string[]; question_ids: string[]; instruction?: string | null; expected_revision?: string | null };
      if (state.orders.some((order) => order.status === "pending")) {
        await route.fulfill({ status: 409, json: problem(409, "Conflict", `rework order '${state.orders[0].id}' of phase requirements is pending`) });
        return true;
      }
      if (body.comment_ids.length === 0 && body.question_ids.length === 0 && !body.instruction) {
        await route.fulfill({ status: 422, json: problem(422, "Unprocessable Entity", "a rework order needs at least one comment, one answered question or an instruction") });
        return true;
      }
      const order: ReworkOrder = {
        id: `rw_e2e_${state.orders.length + 1}`,
        change_id: changeId,
        phase: body.phase ?? "requirements",
        revisions: { [E2E_SPEC_PATH]: state.head },
        comment_ids: body.comment_ids,
        question_ids: body.question_ids,
        instruction: body.instruction ?? null,
        issued_by: "operator@example",
        status: "pending",
        round: null,
        run_id: null,
        summary: null,
        escalation_reason: null,
        created_at: "2026-09-05T10:20:00Z",
        updated_at: "2026-09-05T10:20:00Z",
      };
      state.orders.push(order);
      for (const comment of state.comments) {
        if (body.comment_ids.includes(comment.id)) {
          comment.rework_order_id = order.id;
        }
      }
      state.decisions.push({
        id: `dec_e2e_${state.decisions.length + 1}`,
        gate: "specification",
        outcome: "rejected",
        decided_by: "human",
        role: null,
        decided_at: "2026-09-05T10:20:00Z",
        commit_sha: state.head,
        comment: body.instruction ?? `rework order ${order.id}`,
        evidence_ids: [],
      });
      await route.fulfill({ status: 201, json: order });
      return true;
    }
  }
  return false;
}

export const E2E_TOKEN = "synthetic-e2e-operator-token";
export const TOKEN_STORAGE_KEY = "dark_factory_operator_token";
