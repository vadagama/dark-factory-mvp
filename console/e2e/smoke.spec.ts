/**
 * Playwright smoke suite (T036 DoD, extended for T075/T076 and the M2
 * ChangeSet workspace T088–T090): one scenario per console screen, run against
 * `vite preview` with the API stubbed by page.route (synthetic data only —
 * ADR-021 p.4, no secrets in fixtures). ADR-037 IA: products are the index;
 * /changes/:id is the ChangeSet workspace, the M1 card lives at
 * /changes/:id/card; the service screens moved under /service with redirects.
 */

import { expect, test } from "@playwright/test";
import {
  E2E_DEV_URL,
  E2E_HEAD,
  E2E_SCR1_PATH,
  E2E_SPEC_CONTENT,
  E2E_SPEC_PATH,
  E2E_TOKEN,
  E2E_UI_NOT_REQUIRED_REASON,
  TOKEN_STORAGE_KEY,
  stubApi,
} from "./fixtures/api";

test.beforeEach(async ({ page }) => {
  await stubApi(page);
});

test("screen 1: changes list with statuses and fail-closed intake", async ({ page }) => {
  await page.goto("/changes");
  const table = page.getByTestId("changes-table");
  await expect(table).toContainText("chg_demo_001");
  await expect(table).toContainText("Add /health endpoint");
  await expect(table).toContainText("github/acme/demo-service");
  await expect(table).toContainText("succeeded");
  await expect(table).toContainText("chg_demo_002");
  await expect(table).toContainText("running");

  await expect(table.getByRole("link", { name: "chg_demo_001" })).toHaveAttribute(
    "href",
    "/changes/chg_demo_001",
  );

  // Intake is hidden behind a token: no silent mutations without changes:write.
  await expect(page.getByTestId("intake-token-hint")).toBeVisible();
  await expect(page.getByRole("button", { name: "Создать изменение" })).toBeDisabled();
});

test("screen 2: change card (M1, /card) with stages, evidence, usage and the stage chain", async ({ page }) => {
  await page.goto("/changes/chg_demo_001/card");
  await expect(page.getByRole("heading", { name: "Add /health endpoint" })).toBeVisible();
  await expect(page.getByText("источник: console")).toBeVisible();
  await expect(page.getByText("решений: 1")).toBeVisible();

  const stages = page.getByTestId("stages-run_demo_001");
  await expect(stages).toContainText("specification");
  await expect(stages).toContainText("review verification");
  await expect(stages).toContainText("release");

  await expect(page.getByTestId("open-blockers-run_demo_001")).toHaveText("1");
  await expect(page.getByText("Fix failing integration tests")).toBeVisible();

  const evidence = page.getByTestId("evidence-run_demo_001");
  await expect(evidence).toContainText("ev_001");
  await expect(evidence.getByRole("link", { name: "ev_001" })).toHaveAttribute(
    "href",
    "https://ci.example.com/jobs/1",
  );

  await expect(page.getByText(/токены: 16\s800 \(prompt 12\s500 \/ completion 4\s300\)/)).toBeVisible();
  await expect(page.getByText("стоимость: $0.2847")).toBeVisible();

  // SC-007: the canonical five-stage chain is visible per run.
  const chainTable = page.getByRole("table").filter({ hasText: "Попытка" }).last();
  await expect(chainTable).toContainText("specification");
  await expect(chainTable).toContainText("planning");
  await expect(chainTable).toContainText("construction");
  await expect(chainTable).toContainText("review verification");
  await expect(chainTable).toContainText("release");
});

test("screen 3: gates, approvals history and a version-bound approval POST", async ({ page }) => {
  // Operator token seeded via localStorage — synthetic, never a real secret.
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_STORAGE_KEY, E2E_TOKEN],
  );
  const { posts } = await stubApi(page);

  await page.goto("/changes/chg_demo_001/gates");
  const gates = page.getByTestId("gates-run_demo_001");
  await expect(gates).toContainText("review");
  await expect(gates).toContainText("passed");
  await expect(gates).toContainText("abc1234def");

  await expect(page.getByTestId("blockers-run_demo_001")).toContainText("fnd_001");
  const approvals = page.getByTestId("approvals-table");
  await expect(approvals).toContainText("review");
  await expect(approvals).toContainText("approved");
  await expect(approvals).toContainText("human");

  await page.getByTestId("approval-form");
  await page.getByLabel("subject_revision").fill("deadbeef1234");
  await page.getByLabel("Комментарий (необязательно)").fill("smoke approval");
  await page.getByRole("button", { name: "Записать решение" }).click();

  await expect(page.getByText("Решение записано: dec_new001")).toBeVisible();

  const approvalPost = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_001/approvals");
  expect(approvalPost).toBeDefined();
  expect(approvalPost?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(approvalPost?.headers["idempotency-key"]).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  );
  expect(approvalPost?.body).toMatchObject({
    gate: "review",
    outcome: "approved",
    subject_revision: "deadbeef1234",
    expected_state_revision: 2,
  });
});

test("screen 4: budgets show configured limits and per-run usage", async ({ page }) => {
  await page.goto("/service/budgets");
  const limits = page.getByTestId("limits-table");
  await expect(limits).toContainText("Rework");
  await expect(limits).toContainText("3");
  await expect(limits).toContainText("не задан");

  const usage = page.getByTestId("usage-table");
  await expect(usage).toContainText("run_demo_001");
  await expect(usage).toContainText("$0.2847");
  await expect(usage).toContainText("run_demo_002");
});

test("screen 5: settings manage the masked token and show mode and profiles", async ({ page }) => {
  await page.goto("/service/settings");
  await expect(page.getByTestId("token-absent")).toBeVisible();

  const input = page.getByLabel("Новый токен");
  await expect(input).toHaveAttribute("type", "password");
  await input.fill(E2E_TOKEN);
  await page.getByRole("button", { name: "Сохранить токен" }).click();

  const masked = page.getByTestId("token-masked");
  await expect(masked).toContainText("••••••••oken");
  // The full value never appears in the DOM.
  await expect(masked).not.toContainText(E2E_TOKEN);
  expect(await page.evaluate(([key]) => window.localStorage.getItem(key), [TOKEN_STORAGE_KEY])).toBe(
    E2E_TOKEN,
  );

  await page.getByRole("button", { name: "Удалить токен" }).click();
  await expect(page.getByTestId("token-absent")).toBeVisible();

  await expect(page.getByTestId("factory-mode")).toContainText("С согласованиями");
  const profiles = page.getByTestId("profiles-list");
  await expect(profiles).toContainText("Develop");
  await expect(profiles).toContainText("Product");
  await expect(profiles).toContainText("Quality");
});

test("screen 6: CI stages list with switches and a stage toggle PUT", async ({ page }) => {
  const { writes } = await stubApi(page);
  await page.goto("/service/ci");

  await expect(page.getByRole("heading", { name: "Этапы CI" })).toBeVisible();
  await expect(page.getByTestId("ci-repository")).toContainText("vadagama/dark-factory-mvp");
  await expect(page.getByTestId("ci-safety-banner")).toContainText("Перед merge верните");

  await expect(page.getByTestId("ci-stage-lint")).toContainText("Ruff lint + format");
  await expect(page.getByTestId("ci-stage-lint")).toContainText("CI_SKIP_LINT");
  await expect(page.getByText("Python-гейты")).toBeVisible();
  await expect(page.getByText("Factory-гейты (dogfooding)")).toBeVisible();
  await expect(page.getByText("Console-гейты")).toBeVisible();
  await expect(page.getByText("Доверенные сборки образов")).toBeVisible();
  await expect(page.getByTestId("ci-stage-state-console-e2e")).toHaveText("выключен");

  const lintSwitch = page.getByRole("switch", { name: "Переключатель этапа lint" });
  await expect(lintSwitch).toHaveAttribute("aria-checked", "true");
  await lintSwitch.click();

  await expect(page.getByTestId("ci-stage-state-lint")).toHaveText("выключен");
  const toggle = writes.find((write) => write.pathname === "/api/v1/ci/stages/lint");
  expect(toggle).toBeDefined();
  expect(toggle?.method).toBe("PUT");
  expect(toggle?.body).toEqual({ enabled: false });
});

// --- ADR-037 IA, T075/T076 -----------------------------------------------------

test("products → product page with NextStep → intake with agent brief → change page", async ({ page }) => {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_STORAGE_KEY, E2E_TOKEN],
  );
  const { posts } = await stubApi(page);

  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Основная навигация" });
  await expect(nav).toContainText("Продукты");
  await expect(nav).toContainText("Требует внимания");
  await expect(nav).toContainText("Активность");
  await expect(nav).toContainText("Служебное");

  const products = page.getByTestId("products-table");
  await expect(products).toContainText("Demo service");
  await expect(products).toContainText("github/acme/demo-service");
  await expect(products).toContainText("ready");
  await expect(products).toContainText("Legacy API");
  await expect(products).toContainText("created");
  // One change belongs to prd_demo_001, none to prd_demo_002.
  await expect(page.getByTestId("product-row-prd_demo_001")).toContainText("1");
  await expect(page.getByTestId("product-form")).toBeVisible();

  await products.getByRole("link", { name: "Demo service" }).click();
  await expect(page).toHaveURL(/\/products\/prd_demo_001$/);
  await expect(page.getByTestId("product-header")).toContainText("Demo service");
  const nextStep = page.getByTestId("next-step");
  await expect(nextStep).toContainText("Продукт готов к работе");
  await expect(page.getByTestId("next-step-primary")).toHaveText("Новая фича");
  await expect(page.getByTestId("product-changes-table")).toContainText("Add /health endpoint");
  await expect(page.getByTestId("product-baseline")).toContainText("baseline/2026-09");
  await expect(page.getByText("Просмотр документов baseline появится в M2.")).toBeVisible();
  await expect(page.getByText("История доставок появится в M5.")).toBeVisible();

  await page.getByTestId("next-step-primary").click();
  await expect(page).toHaveURL(/\/products\/prd_demo_001\/new-change$/);
  await expect(page.getByTestId("intake-product-header")).toContainText("Demo service");

  await page.getByLabel("Название").fill("Health endpoint");
  await page.getByLabel("Опишите своими словами").fill("Нужен health endpoint для демо-сервиса");
  await page.getByTestId("formulate-button").click();
  await expect(page.getByLabel("Проблема")).toHaveValue("Нет проверки живости сервиса");
  await expect(page.getByLabel("Цель")).toHaveValue("Эндпоинт /health отвечает 200 при готовности");
  await expect(page.getByTestId("brief-author")).toContainText("агент");

  await page.getByLabel(/Только спецификации/).check();
  await page.getByLabel("Лимит, USD").fill("25");
  await expect(page.getByTestId("intake-forecast")).toContainText("Прогноз расхода появится после первого прогона; лимит: 25 USD");
  await page.getByRole("button", { name: "Создать задачу" }).click();

  await expect(page).toHaveURL(/\/changes\/chg_[0-9a-f]{12}$/);
  await expect(page.getByRole("heading", { name: "Health endpoint" })).toBeVisible();
  await expect(page.getByTestId("next-step")).toContainText("Бриф готов — можно запускать фазу «Требования»");
  await expect(page.getByTestId("next-step-cli")).toContainText("factory run advance --change-id chg_");
  await expect(page.getByTestId("brief-section")).toContainText("Нет проверки живости сервиса");
  await expect(page.getByTestId("brief-section")).toContainText("complete");
  await expect(page.getByTestId("change-intake-line")).toContainText("сценарий: только спецификации");
  await expect(page.getByTestId("change-intake-line")).toContainText("лимит: 25 USD");

  const formulate = posts.find((post) => post.pathname === "/api/v1/briefs/formulate");
  expect(formulate?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(formulate?.body).toEqual({ source_text: "Нужен health endpoint для демо-сервиса" });
  const created = posts.find((post) => post.pathname === "/api/v1/changes");
  expect(created?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(created?.body).toMatchObject({
    source: "console",
    product_id: "prd_demo_001",
    product: { provider: "github", slug: "acme/demo-service" },
    scenario: "specs_only",
    spend_limit: { cost_budget_usd: "25", token_budget: null },
    brief: { status: "complete", formulated_by: "agent", problem: "Нет проверки живости сервиса" },
  });
});

test("intake: formulate failure keeps the brief a draft with an observable error", async ({ page }) => {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_STORAGE_KEY, E2E_TOKEN],
  );
  await stubApi(page);
  await page.goto("/products/prd_demo_001/new-change");
  await page.getByLabel("Опишите своими словами").fill("[harness-down] нужен health endpoint");
  await page.getByTestId("formulate-button").click();
  const notice = page.getByText(/Бриф остался черновиком/);
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("Agent harness is not configured on this contour");
  // The fields stay editable for the operator.
  await expect(page.getByLabel("Проблема")).toBeEditable();
  await expect(page.getByLabel("Проблема")).toHaveValue("");
  await expect(page.getByTestId("brief-author")).toContainText("оператор");
});

test("product page: validate repository shows the observed state and the 503 detail", async ({ page }) => {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_STORAGE_KEY, E2E_TOKEN],
  );
  const { posts } = await stubApi(page);

  await page.goto("/products/prd_demo_002");
  await expect(page.getByTestId("next-step")).toContainText("репозиторий ещё не проверен");
  await expect(page.getByTestId("next-step-blockers")).toContainText("снимает: оператор");
  await page.getByTestId("next-step-primary").click();
  await expect(page.getByText(/Репозиторий проверен/)).toContainText("baseline absent");
  expect(posts.find((post) => post.pathname === "/api/v1/products/prd_demo_002/validate")?.headers.authorization).toBe(
    `Bearer ${E2E_TOKEN}`,
  );

  await page.goto("/products/prd_demo_001");
  await page.getByTestId("validate-button").click();
  await expect(page.getByRole("alert")).toContainText("Repository provisioning is not configured on this contour");
});

test("products: fail-closed registration without a token, then a POST with a token", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("product-token-hint")).toBeVisible();
  await expect(page.getByRole("button", { name: "Добавить продукт" })).toBeDisabled();

  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_STORAGE_KEY, E2E_TOKEN],
  );
  const { posts } = await stubApi(page);
  await page.goto("/");
  await page.getByLabel("ID").fill("prd_e2e_001");
  await page.getByLabel("Название").fill("Billing");
  await page.getByLabel("Репозиторий (slug)").fill("acme/billing");
  await page.getByRole("button", { name: "Добавить продукт" }).click();
  await expect(page).toHaveURL(/\/products\/prd_e2e_001$/);
  await expect(page.getByTestId("product-header")).toContainText("Billing");
  await expect(page.getByTestId("product-header")).toContainText("created");
  const post = posts.find((candidate) => candidate.pathname === "/api/v1/products");
  expect(post?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(post?.body).toMatchObject({ id: "prd_e2e_001", name: "Billing", repository: { provider: "github", slug: "acme/billing" } });
});

test("change card: brief editor saves via PUT and the guidance re-reads", async ({ page }) => {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_STORAGE_KEY, E2E_TOKEN],
  );
  const { writes } = await stubApi(page);
  await page.goto("/changes/chg_demo_001/card");
  await expect(page.getByTestId("next-step")).toContainText("Задача завершена");
  await expect(page.getByTestId("brief-section")).toContainText("сформулировал: агент");
  await page.getByLabel("Цель").fill("Эндпоинт /health отвечает 200 и покрыт тестом");
  await page.getByRole("button", { name: "Сохранить бриф" }).click();
  await expect(page.getByText("Бриф сохранён.")).toBeVisible();
  const put = writes.find((write) => write.pathname === "/api/v1/changes/chg_demo_001/brief");
  expect(put?.method).toBe("PUT");
  expect(put?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(put?.body).toMatchObject({ goal: "Эндпоинт /health отвечает 200 и покрыт тестом", formulated_by: "operator", status: "complete" });
});

test("placeholders and redirects: attention, activity, /budgets → /service/budgets", async ({ page }) => {
  await page.goto("/attention");
  await expect(page.getByRole("status")).toContainText("Inbox «Требует внимания» появится в M5 (T116).");
  await page.goto("/activity");
  await expect(page.getByRole("status")).toContainText("Лента активности вне объёма MVP.");

  await page.goto("/budgets");
  await expect(page).toHaveURL(/\/service\/budgets$/);
  await expect(page.getByTestId("limits-table")).toBeVisible();
  await page.goto("/ci");
  await expect(page).toHaveURL(/\/service\/ci$/);
  await page.goto("/settings");
  await expect(page).toHaveURL(/\/service\/settings$/);

  await page.goto("/service");
  await expect(page.getByTestId("service-links")).toContainText("Бюджеты");
  await expect(page.getByRole("link", { name: "Служебное" })).toHaveClass(/active/);
});

// --- M2: ChangeSet workspace, requirements phase, markdown editor (T088–T090) ----

function seedToken(page: import("@playwright/test").Page) {
  return page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [TOKEN_STORAGE_KEY, E2E_TOKEN],
  );
}

test("changeset: the workspace shell — panels, phases, tabs, one CTA, no percentage", async ({ page }) => {
  await page.goto("/changes/chg_demo_003");
  await expect(page.getByRole("heading", { name: "Add rate limiting" })).toBeVisible();
  const top = page.getByTestId("workspace-top");
  await expect(top).toContainText("chg_demo_003");
  await expect(page.getByTestId("workspace-phase")).toContainText("Требования");
  await expect(page.getByTestId("workspace-budget")).toContainText("факт $0.1200 · лимит 40.0000 USD · прогноз —");
  await expect(page.getByTestId("workspace-blockers")).toHaveText("1");

  const phases = page.getByTestId("workspace-phases");
  for (const label of ["Инициатива", "Требования", "Архитектура", "Интерфейс", "План", "Исполнение", "Демонстрация", "Доставка"]) {
    await expect(phases).toContainText(label);
  }
  await expect(page.getByTestId("phase-requirements")).toContainText("вопросов 2 · замечаний 0 · итерация 0");
  await expect(page.getByTestId("phase-requirements")).toContainText("в работе");

  const tabs = page.getByRole("tablist");
  await expect(tabs).toContainText("Результат");
  await expect(tabs).toContainText("Изменения");
  await expect(tabs).toContainText("Проверки");
  await expect(tabs).toContainText("История");
  await expect(page.getByTestId("context-panel")).toBeVisible();
  await page.getByTestId("context-toggle").click();
  await expect(page.getByTestId("context-panel")).toHaveCount(0);
  await page.getByTestId("context-toggle").click();

  await expect(page.getByTestId("workspace-decision").getByTestId("next-step-primary")).toHaveCount(1);
  await expect(page.getByTestId("next-step-primary")).toHaveText("Ответить на вопросы");

  // Requirements delta with stable ids; other phases are honest placeholders.
  await expect(page.getByTestId("anchors-REQ-001-rate-limit.md")).toContainText("AC-1");
  await page.getByTestId("tab-checks").click();
  await expect(page.getByTestId("phase-gate")).toContainText("закрыт");
  await expect(page.getByTestId("phase-gate-counts")).toContainText("0 из 3");
  // Ф2/Ф3 are real workspaces since M3; the placeholders start at Ф4.
  await page.getByTestId("phase-plan").click();
  await expect(page.getByTestId("phase-placeholder")).toContainText("появится в M4");
  await expect(page.getByTestId("phase-plan")).toContainText("не начата");

  const text = await page.locator("body").innerText();
  expect(text).not.toMatch(/\d+\s?%/);
});

test("changeset: honest states when the contour has no repository", async ({ page }) => {
  await page.goto("/changes/chg_demo_002");
  await expect(page.getByRole("heading", { name: "Split legacy billing module" })).toBeVisible();
  await page.getByTestId("tab-changes").click();
  await expect(page.getByRole("tabpanel")).toContainText("the product repository is not configured in this contour");
});

test("requirements: answer a question in one click, then approve on the gate revision", async ({ page }) => {
  await seedToken(page);
  const { posts } = await stubApi(page);
  await page.goto("/changes/chg_demo_003");
  const question = page.getByTestId("question-q_e2e_choice");
  await expect(question).toContainText("блокирует гейт");
  await page.getByTestId("options-q_e2e_choice").getByRole("button", { name: "429" }).click();
  await expect(page.getByTestId("answer-q_e2e_choice")).toContainText("Ответ: 429");
  const answer = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_003/questions/q_e2e_choice/answer");
  expect(answer?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(answer?.body).toEqual({ value: "429" });

  // The assumption is confirmed with one click too.
  await page.getByTestId("question-q_e2e_assume").getByRole("button", { name: "Подтвердить" }).click();
  await expect(page.getByTestId("answer-q_e2e_assume")).toContainText("confirmed");

  // The gate opened: the guidance primary is now the approval.
  await expect(page.getByTestId("next-step-primary")).toHaveText("Согласовать требования");
  await page.getByTestId("next-step-primary").click();
  await expect(page.getByTestId("approval-revision")).toHaveText(E2E_HEAD.slice(0, 12));
  await page.getByTestId("approval-comment").fill("требования согласованы в smoke");
  await page.getByTestId("approval-submit").click();
  await expect(page.getByTestId("approval-recorded")).toContainText("Решение записано: dec_e2e_1");
  const approval = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_003/approvals");
  expect(approval?.body).toMatchObject({ gate: "specification", outcome: "approved", subject_revision: E2E_HEAD, comment: "требования согласованы в smoke" });
  await expect(page.getByTestId("phase-requirements")).toContainText("согласована");
});

test("requirements: leave a comment on a fragment, then send to rework", async ({ page }) => {
  await seedToken(page);
  const { posts } = await stubApi(page);
  await page.goto("/changes/chg_demo_003");
  await expect(page.getByTestId("anchors-REQ-001-rate-limit.md")).toBeVisible();
  // Picking an anchor chip selects the fragment for the composer.
  await page.getByTestId("anchors-REQ-001-rate-limit.md").getByRole("button", { name: "AC-2" }).click();
  await expect(page.getByTestId("context-fragment")).toContainText("AC-2");
  await page.getByTestId("comment-body").fill("Уточнить, кто меняет лимит.");
  await page.getByTestId("comment-submit").click();
  await expect(page.getByTestId("comment-cmt_e2e_1")).toContainText("Уточнить, кто меняет лимит.");
  const comment = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_003/comments");
  expect(comment?.body).toEqual({ artifact: E2E_SPEC_PATH, anchor_id: "AC-2", revision: E2E_HEAD, body: "Уточнить, кто меняет лимит.", phase: "requirements" });
  // A comment never starts rework by itself.
  await expect(page.getByTestId("context-rework")).toContainText("Поручения (0)");

  await page.getByTestId("rework-toggle").click();
  await page.getByTestId("rework-instruction").fill("Описать, кто и как меняет лимит.");
  await page.getByTestId("rework-submit").click();
  await expect(page.getByTestId("rework-rw_e2e_1")).toContainText("ожидает раунда");
  await expect(page.getByTestId("rework-rw_e2e_1")).toContainText("Раунд ещё не запущен");
  const order = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_003/rework-orders");
  expect(order?.body).toMatchObject({ phase: "requirements", comment_ids: ["cmt_e2e_1"], instruction: "Описать, кто и как меняет лимит.", expected_revision: E2E_HEAD });
  // The gate is closed by the pending order; the history shows the send-back as a rejected decision.
  await expect(page.getByTestId("next-step")).toContainText("Отправлено на доработку");
  await page.getByTestId("tab-history").click();
  await expect(page.getByTestId("history-decisions")).toContainText("rejected");
});

test("editor: modes keep the content verbatim, the draft autosaves, the commit goes to git", async ({ page }) => {
  await seedToken(page);
  const { writes } = await stubApi(page);
  await page.goto("/changes/chg_demo_003");
  await page.getByTestId("delta-REQ-001-rate-limit.md").getByRole("button", { name: "REQ-001-rate-limit.md" }).click();
  const editor = page.getByTestId("markdown-editor");
  await expect(editor).toBeVisible();
  await expect(page.getByTestId("editor-properties")).toContainText("защищено");
  await expect(page.getByTestId("editor-rendered")).toContainText("Rate limiting");

  await page.getByTestId("editor-mode-markdown").click();
  await expect(page.getByTestId("editor-textarea")).toHaveValue(E2E_SPEC_CONTENT);
  await page.getByTestId("editor-mode-reading").click();
  await expect(page.getByTestId("editor-textarea")).toHaveCount(0);
  await expect(page.getByTestId("editor-rendered")).toContainText("configurable without a redeploy");
  await page.getByTestId("editor-mode-markdown").click();
  await expect(page.getByTestId("editor-textarea")).toHaveValue(E2E_SPEC_CONTENT);
  await expect(page.getByTestId("editor-save-state")).toHaveText("Без изменений");
  expect(writes.filter((write) => write.pathname.includes("/artifact"))).toHaveLength(0);

  const edited = `${E2E_SPEC_CONTENT}- AC-3: the response carries Retry-After.\n`;
  await page.getByTestId("editor-textarea").fill(edited);
  await expect(page.getByTestId("editor-save-state")).toHaveText("Не сохранено");
  await expect(page.getByTestId("editor-save-state")).toHaveText("Черновик сохранён 10:30");
  const draft = writes.find((write) => write.method === "PUT" && write.pathname.includes("/artifact-drafts/"));
  expect(draft?.pathname).toBe(`/api/v1/changes/chg_demo_003/artifact-drafts/${E2E_SPEC_PATH}`);
  expect(draft?.body).toEqual({ content: edited, base_revision: E2E_HEAD });

  await page.getByLabel("Сообщение коммита (необязательно)").fill("spec: add AC-3");
  await page.getByTestId("editor-save-git").click();
  await expect(page.getByTestId("artifact-editor-pane")).toContainText("Сохранено в git: ревизия e2e0002e2e00");
  const commit = writes.find((write) => write.method === "PUT" && write.pathname === `/api/v1/changes/chg_demo_003/artifacts/${E2E_SPEC_PATH}`);
  expect(commit?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(commit?.body).toEqual({ content: edited, base_revision: E2E_HEAD, message: "spec: add AC-3", properties: null });

  // The editor reloaded on the new revision: the committed text is the source now, the draft is gone.
  await page.getByTestId("editor-mode-markdown").click();
  await expect(page.getByTestId("editor-textarea")).toHaveValue(edited);
  await expect(page.getByTestId("editor-save-state")).toHaveText("Без изменений");
  await page.getByTestId("editor-history-toggle").click();
  await expect(page.getByTestId("editor-history")).toContainText("spec: add AC-3");
  await expect(page.getByTestId("editor-history")).toContainText("spec: initial requirements");
  await page.getByTestId("editor-show-diff").click();
  await expect(page.getByTestId("editor-diff")).toContainText("+- AC-3: the response carries Retry-After.");
});

// --- M3: architecture and interface phases, UI gate (T095–T097) --------------------

test("architecture: decisions overview, ADR as document, request alternative inline", async ({ page }) => {
  await seedToken(page);
  const { posts } = await stubApi(page, { phase: "architecture" });
  await page.goto("/changes/chg_demo_003");
  await expect(page.getByRole("heading", { name: "Add rate limiting" })).toBeVisible();

  // The left column comes from GET /phases: the earlier phase is approved, this one waits for a decision.
  await expect(page.getByTestId("phase-architecture")).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("phase-architecture")).toContainText("ждёт решения");
  await expect(page.getByTestId("phase-architecture")).toHaveAttribute("title", "Гейт открыт: решение оператора");
  await expect(page.getByTestId("phase-requirements")).toContainText("согласована");
  await expect(page.getByTestId("workspace-phase")).toContainText("Архитектура");

  // The design overview renders with its mermaid diagram (lazy chunk, real engine in Chromium).
  const overview = page.getByTestId("design-overview");
  await expect(overview).toContainText("Лимит проверяется в middleware");
  await expect(page.getByTestId("mermaid-rendered").locator("svg")).toBeVisible();
  await expect(overview).toContainText("Компоненты");

  // Decision cards with the derived status, alternatives and impact.
  const first = page.getByTestId("decision-adr:prd_demo_002:0001");
  await expect(first).toContainText("Token bucket per client");
  await expect(first).toContainText("предложено");
  await expect(page.getByTestId("alternatives-adr:prd_demo_002:0001")).toContainText("двойной всплеск на границе окна");
  await expect(page.getByTestId("impact-adr:prd_demo_002:0001")).toContainText("public_api");

  // The ADR opens as a document in the editor pane, with protected frontmatter keys.
  await page.getByTestId("open-adr-adr:prd_demo_002:0001").click();
  await expect(page.getByTestId("artifact-editor-pane")).toContainText("ADR-001-token-bucket.md");
  await expect(page.getByTestId("editor-properties")).toContainText("защищено");
  await expect(page.getByTestId("editor-rendered")).toContainText("Всплески трафика роняют API");
  await page.getByTestId("editor-back").click();

  // «Запросить альтернативу» is an inline form on the card: nothing leaves the screen.
  const second = page.getByTestId("decision-adr:prd_demo_002:0002");
  await second.getByTestId("alternative-toggle-adr:prd_demo_002:0002").click();
  await page.getByTestId("alternative-instruction-adr:prd_demo_002:0002").fill("Рассмотреть счётчики в памяти процесса.");
  await page.getByTestId("alternative-submit-adr:prd_demo_002:0002").click();
  await expect(second).toContainText("Поручение rw_e2e_1 создано");
  await expect(page.getByTestId("pending-adr:prd_demo_002:0002")).toContainText("ожидает раунда");
  await expect(second).toContainText("требует пересмотра");
  await expect(page.getByTestId("decisions-overview")).toBeVisible();
  const alternative = posts.find((post) => decodeURIComponent(post.pathname) === "/api/v1/changes/chg_demo_003/decisions/adr:prd_demo_002:0002/alternative");
  expect(alternative).toBeDefined();
  expect(alternative?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(alternative?.body).toEqual({ instruction: "Рассмотреть счётчики в памяти процесса.", comment_ids: [] });

  // A second order on the phase is refused with the server text, shown on the card.
  await page.getByTestId("alternative-toggle-adr:prd_demo_002:0001").click();
  await page.getByTestId("alternative-instruction-adr:prd_demo_002:0001").fill("Sliding window?");
  await page.getByTestId("alternative-submit-adr:prd_demo_002:0001").click();
  await expect(first).toContainText("rework order 'rw_e2e_1' of phase architecture is pending");

  // The guidance follows the order; the history shows the phase of the recorded (rejected) decision.
  await expect(page.getByTestId("next-step")).toContainText("запрошена альтернатива");
  await page.getByTestId("tab-history").click();
  await expect(page.getByTestId("history-decisions")).toContainText("Архитектура");
  await expect(page.getByTestId("history-decisions")).toContainText("rejected");
  await expect(page.getByTestId("history-tab")).toContainText("решения: adr:prd_demo_002:0002");

  const text = await page.locator("body").innerText();
  expect(text).not.toMatch(/\d+\s?%/);
});

test("interface: scenarios by default, screen gallery with states and dev link, comment on an element with a detached mark", async ({ page }) => {
  await seedToken(page);
  const { posts } = await stubApi(page, { phase: "interface" });
  await page.goto("/changes/chg_demo_003");
  await expect(page.getByTestId("phase-interface")).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("phase-interface")).toContainText("ждёт решения");
  await expect(page.getByTestId("phase-architecture")).toContainText("согласована");

  // «Сценарии» opens first; a step chip jumps to its screen in the gallery.
  await expect(page.getByTestId("ui-tab-scenarios")).toHaveAttribute("aria-selected", "true");
  const scenario = page.getByTestId("scenario-SCN-001");
  await expect(scenario).toContainText("Клиент превышает лимит");
  await expect(page.getByTestId("step-SCN-001-S1")).toContainText("Открыть страницу лимитов");
  await page.getByTestId("step-SCN-001-S2").getByRole("button", { name: "SCR-002" }).click();
  await expect(page.getByTestId("ui-tab-screens")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("screen-SCR-002")).toHaveClass(/screen-card--focused/);

  // The gallery: five states, a missing one is an explicit «не описано»; dev link vs honest text.
  const gallery = page.getByTestId("ui-gallery");
  await expect(gallery.getByRole("article")).toHaveCount(2);
  await expect(page.getByTestId("state-SCR-001-error")).toContainText("Сервис лимитов недоступен");
  await expect(page.getByTestId("state-SCR-002-error")).toContainText("ошибка: не описано");
  await expect(page.getByTestId("state-SCR-002-success")).toContainText("Таблица отказов");
  const devLink = page.getByTestId("dev-link-SCR-001");
  await expect(devLink).toHaveAttribute("href", `${E2E_DEV_URL}/limits`);
  await expect(devLink).toHaveAttribute("target", "_blank");
  await expect(devLink).toHaveAttribute("rel", "noreferrer");
  await expect(page.getByTestId("dev-missing-SCR-002")).toContainText("dev-окружение появится после доставки");
  await expect(page.getByTestId("element-SCR-001-EL-save")).toContainText("Button");

  // The seeded remark points at an element the designer removed: the lost anchor is explicit, in the gallery and in the context panel.
  await expect(page.getByTestId("orphaned-SCR-001")).toContainText("привязка потеряна");
  await expect(page.getByTestId("orphaned-SCR-001")).toContainText("cmt_e2e_detached (EL-old-banner)");
  await expect(page.getByTestId("comment-cmt_e2e_detached")).toContainText("якорь потерян");

  // «Комментарий» on an element prefills the composer with the screen and the EL anchor; the POST carries them.
  await page.getByTestId("comment-SCR-001-EL-limit-input").click();
  await expect(page.getByTestId("context-fragment")).toContainText("EL-limit-input");
  await page.getByTestId("comment-body").fill("Подсказать единицы измерения.");
  await page.getByTestId("comment-submit").click();
  // The seeded detached remark is cmt_e2e_1 in this variant's numbering; the new one is the second.
  await expect(page.getByTestId("comment-cmt_e2e_2")).toContainText("Подсказать единицы измерения.");
  await expect(page.getByTestId("element-SCR-001-EL-limit-input")).toContainText("замечаний: 1");
  const comment = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_003/comments");
  expect(comment?.body).toEqual({ artifact: E2E_SCR1_PATH, anchor_id: "EL-limit-input", revision: E2E_HEAD, body: "Подсказать единицы измерения.", phase: "interface" });

  // Links: from → to with trigger and condition.
  await page.getByTestId("ui-tab-links").click();
  await expect(page.getByTestId("link-SCR-001->SCR-002")).toContainText("клик «История»");
  await expect(page.getByTestId("link-SCR-001->SCR-002")).toContainText("есть отказы");

  // Approving the interface records the decision on gate ui with the phase; the column follows.
  await expect(page.getByTestId("next-step-primary")).toHaveText("Согласовать интерфейс");
  await page.getByTestId("next-step-primary").click();
  await expect(page.getByTestId("approval-revision")).toHaveText(E2E_HEAD.slice(0, 12));
  await page.getByTestId("approval-submit").click();
  await expect(page.getByTestId("approval-recorded")).toContainText("Решение записано");
  const approval = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_003/approvals");
  expect(approval?.body).toMatchObject({ gate: "ui", phase: "interface", outcome: "approved", subject_revision: E2E_HEAD });
  await expect(page.getByTestId("phase-interface")).toContainText("согласована");
});

test("ui gate: planned checks are not green; backend-only shows Не требуется and waives with a reason", async ({ page }) => {
  // A UI change: axe / visual regression are planned for execution, never a green status before it.
  await seedToken(page);
  await stubApi(page, { phase: "interface" });
  await page.goto("/changes/chg_demo_003");
  await page.getByTestId("tab-checks").click();
  await expect(page.getByTestId("phase-gate")).toContainText("фаза: Интерфейс");
  await expect(page.getByTestId("check-axe")).toContainText("запланировано на исполнении");
  await expect(page.getByTestId("check-visual_regression")).toContainText("запланировано на исполнении");
  await expect(page.getByTestId("phase-gate-checks").locator(".badge--success")).toHaveCount(0);
  await expect(page.getByTestId("phase-gate-ui-requirement")).toContainText("UI требуется");
  await expect(page.getByTestId("waive-ui")).toHaveCount(0);

  // A backend-only change: the architect proposed not_required; the operator confirms with a waived decision.
  const { posts } = await stubApi(page, { phase: "interface", backendOnly: true });
  await page.goto("/changes/chg_demo_003");
  await expect(page.getByTestId("phase-interface")).toContainText("ждёт решения");
  await expect(page.getByTestId("phase-interface")).toHaveAttribute("title", "Архитектор предложил: UI не требуется — подтвердите пропуск");
  await expect(page.getByTestId("interface-empty")).toContainText("UI-спеки на ветке пока нет");
  await expect(page.getByTestId("next-step-primary")).toHaveText("Подтвердить пропуск UI");

  await page.getByTestId("tab-checks").click();
  const requirement = page.getByTestId("phase-gate-ui-not-required");
  await expect(requirement).toContainText("Не требуется");
  await expect(requirement).toContainText(`UI не требуется: ${E2E_UI_NOT_REQUIRED_REASON}`);
  await expect(requirement).toContainText("источник: архитектор");
  await expect(page.getByTestId("check-axe")).toContainText("не требуется");
  await expect(page.getByTestId("phase-gate-checks").locator(".badge--success")).toHaveCount(0);

  // No interface artifacts → no revision: the waiver is still possible and travels without subject_revision (M3).
  await expect(page.getByTestId("phase-gate")).toContainText("ревизия —");
  await expect(page.getByTestId("waive-ui")).toBeEnabled();
  await page.getByTestId("waive-ui").click();
  await expect(page.getByTestId("phase-gate")).toContainText("Пропуск подтверждён оператором.");
  await expect(page.getByTestId("phase-gate-approvals")).toContainText("waived");
  await expect(page.getByTestId("phase-gate-approvals")).toContainText("без ревизии");
  await expect(page.getByTestId("phase-gate-approvals")).toContainText("Интерфейс");
  const waiver = posts.find((post) => post.pathname === "/api/v1/changes/chg_demo_003/approvals");
  expect(waiver?.headers.authorization).toBe(`Bearer ${E2E_TOKEN}`);
  expect(waiver?.body).toEqual({
    gate: "ui",
    phase: "interface",
    outcome: "waived",
    comment: E2E_UI_NOT_REQUIRED_REASON,
    expected_state_revision: 1,
  });
  await expect(page.getByTestId("phase-interface")).toContainText("пропущена");
  await expect(page.getByTestId("next-step")).toContainText("пропущена с основанием");
});
