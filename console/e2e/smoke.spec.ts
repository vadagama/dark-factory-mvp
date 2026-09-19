/**
 * Playwright smoke suite (T036 DoD, extended for T075/T076): one scenario per
 * console screen, run against `vite preview` with the API stubbed by
 * page.route (synthetic data only — ADR-021 p.4, no secrets in fixtures).
 * ADR-037 IA: products are the index; the legacy changes list lives at
 * /changes; the service screens moved under /service with redirects.
 */

import { expect, test } from "@playwright/test";
import { E2E_TOKEN, TOKEN_STORAGE_KEY, stubApi } from "./fixtures/api";

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

test("screen 2: change card with stages, evidence, usage and the stage chain", async ({ page }) => {
  await page.goto("/changes/chg_demo_001");
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
  await page.goto("/changes/chg_demo_001");
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
