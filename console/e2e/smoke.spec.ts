/**
 * Playwright smoke suite (T036 DoD): one scenario per console screen, run
 * against `vite preview` with the API stubbed by page.route (synthetic data
 * only — ADR-021 p.4, no secrets in fixtures).
 */

import { expect, test } from "@playwright/test";
import { E2E_TOKEN, TOKEN_STORAGE_KEY, stubApi } from "./fixtures/api";

test.beforeEach(async ({ page }) => {
  await stubApi(page);
});

test("screen 1: changes list with statuses and fail-closed intake", async ({ page }) => {
  await page.goto("/");
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
  await page.goto("/budgets");
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
  await page.goto("/settings");
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
