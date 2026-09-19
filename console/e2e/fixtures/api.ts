/**
 * Synthetic API fixtures for the Playwright smoke suite (ADR-021 p.4):
 * clearly fake data shaped like the T035 wire contract, served by
 * `page.route` — no real API and no secrets are involved in e2e runs.
 */

import type { Page } from "@playwright/test";
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
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const { pathname, searchParams } = new URL(request.url());
    const method = request.method();

    if (method === "POST" || method === "PUT") {
      const capture = {
        method,
        pathname,
        body: request.postDataJSON(),
        headers: request.headers(),
      };
      writes.push(capture);
      if (method === "POST") {
        posts.push(capture);
      }
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
      const all = [change1, change2, ...createdChanges.values()];
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
      "/api/v1/changes/chg_demo_001/approvals": [approval1],
      "/api/v1/changes/chg_demo_002/approvals": [],
      "/api/v1/runs": [run1Summary, run2Summary],
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

export const E2E_TOKEN = "synthetic-e2e-operator-token";
export const TOKEN_STORAGE_KEY = "dark_factory_operator_token";
