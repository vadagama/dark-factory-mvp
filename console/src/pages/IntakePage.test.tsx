import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { IntakePage } from "./IntakePage";
import * as token from "../api/token";
import { briefComplete, briefDraftWithError, product } from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";
import type { IntakeBrief } from "../api/types";

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/products/prd_demo_001/new-change"]}>
      <Routes>
        <Route path="/products/:productId/new-change" element={<IntakePage />} />
        <Route path="/changes/:changeId" element={<p data-testid="change-marker">change page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

const productRoute: FetchRoute = {
  method: "GET",
  pattern: /\/api\/v1\/products\/prd_demo_001$/,
  handler: () => jsonResponse(200, product),
};

function formulateRoute(brief: IntakeBrief): FetchRoute {
  return { method: "POST", pattern: /\/api\/v1\/briefs\/formulate$/, handler: () => jsonResponse(200, brief) };
}

describe("IntakePage", () => {
  it("is fail-closed without a token", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    stubFetch([productRoute]);
    renderPage();
    await screen.findByTestId("intake-product-header");
    expect(screen.getByTestId("intake-token-hint")).toHaveTextContent("Мутации недоступны");
    expect(screen.getByRole("button", { name: "Создать задачу" })).toBeDisabled();
    expect(screen.getByTestId("formulate-button")).toBeDisabled();
  });

  it("formulates the brief with the agent and submits the change with brief, scenario and limit", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("synthetic-token");
    const fetchMock = stubFetch([
      productRoute,
      formulateRoute(briefComplete),
      { method: "POST", pattern: /\/api\/v1\/changes$/, handler: (_url, init) => jsonResponse(201, bodyOf(init)) },
    ]);
    const user = userEvent.setup();
    renderPage();
    const header = await screen.findByTestId("intake-product-header");
    expect(header).toHaveTextContent("Demo service");

    await user.type(screen.getByLabelText("Название"), "Health endpoint");
    await user.type(screen.getByLabelText("Опишите своими словами"), "Нужен health endpoint для демо-сервиса");
    await user.click(screen.getByTestId("formulate-button"));

    // The four fields are filled from the agent's brief; the marker says so.
    expect((await screen.findByLabelText("Проблема")) as HTMLTextAreaElement).toHaveValue("Нет проверки живости сервиса");
    expect(screen.getByLabelText("Цель")).toHaveValue("Эндпоинт /health отвечает 200 при готовности");
    expect(screen.getByLabelText("Ограничения (по одному в строке)")).toHaveValue("Без новых зависимостей");
    expect(screen.getByLabelText("Вне объёма (по одному в строке)")).toHaveValue("Метрики readiness");
    expect(screen.getByTestId("brief-author")).toHaveTextContent("сформулировал: агент");
    expect(screen.queryByText(/Бриф остался черновиком/)).not.toBeInTheDocument();

    await user.click(screen.getByLabelText(/Только спецификации/));
    await user.type(screen.getByLabelText("Лимит, USD"), "25.5");
    await user.type(screen.getByLabelText("Лимит токенов (необязательно)"), "150000");
    expect(screen.getByTestId("intake-forecast")).toHaveTextContent(
      "Прогноз расхода появится после первого прогона; лимит: 25.5 USD",
    );
    await user.selectOptions(screen.getByLabelText("Класс риска"), "R2");
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));

    await screen.findByTestId("change-marker");
    const posts = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit).method === "POST") as [string, RequestInit][];
    expect(posts).toHaveLength(2);
    const [formulateUrl, formulateInit] = posts[0];
    expect(formulateUrl).toContain("/api/v1/briefs/formulate");
    expect((formulateInit.headers as Record<string, string>).Authorization).toBe("Bearer synthetic-token");
    expect(bodyOf(formulateInit)).toEqual({ source_text: "Нужен health endpoint для демо-сервиса" });

    const body = bodyOf(posts[1][1]);
    expect(String(body.id)).toMatch(/^chg_[0-9a-f]{12}$/);
    expect(body).toMatchObject({
      title: "Health endpoint",
      description: null,
      source: "console",
      external_ref: null,
      product: { provider: "github", slug: "acme/demo-service" },
      product_id: "prd_demo_001",
      risk_class: "R2",
      change_request: null,
      scenario: "specs_only",
      spend_limit: { cost_budget_usd: "25.5", token_budget: 150000 },
      brief: {
        problem: "Нет проверки живости сервиса",
        goal: "Эндпоинт /health отвечает 200 при готовности",
        constraints: ["Без новых зависимостей"],
        out_of_scope: ["Метрики readiness"],
        source_text: "Нужен health endpoint для демо-сервиса",
        status: "complete",
        formulated_by: "agent",
        error: null,
      },
    });
  });

  it("keeps the brief a draft with a visible error when the agent could not formulate, and lets the operator fill it", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("synthetic-token");
    const fetchMock = stubFetch([
      productRoute,
      formulateRoute(briefDraftWithError),
      { method: "POST", pattern: /\/api\/v1\/changes$/, handler: (_url, init) => jsonResponse(201, bodyOf(init)) },
    ]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("intake-product-header");
    await user.type(screen.getByLabelText("Название"), "Health endpoint");
    await user.type(screen.getByLabelText("Опишите своими словами"), "Нужен health endpoint для демо-сервиса");
    await user.click(screen.getByTestId("formulate-button"));

    const notice = await screen.findByText(/Бриф остался черновиком/);
    expect(notice).toHaveTextContent("Agent harness is not configured on this contour");
    expect(notice).toHaveAttribute("role", "status");
    expect(screen.getByTestId("brief-author")).toHaveTextContent("сформулировал: оператор");

    // Fields stay editable: the operator writes the brief by hand.
    await user.type(screen.getByLabelText("Проблема"), "Нет health");
    await user.type(screen.getByLabelText("Цель"), "Есть /health");
    await user.type(screen.getByLabelText("Лимит, USD"), "10");
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));
    await screen.findByTestId("change-marker");

    const posts = fetchMock.mock.calls.filter(([, init]) => (init as RequestInit).method === "POST") as [string, RequestInit][];
    const body = bodyOf(posts[1][1]);
    expect(body.scenario).toBe("full");
    expect(body.spend_limit).toEqual({ cost_budget_usd: "10", token_budget: null });
    expect(body.brief).toMatchObject({
      problem: "Нет health",
      goal: "Есть /health",
      source_text: "Нужен health endpoint для демо-сервиса",
      status: "complete",
      formulated_by: "operator",
    });
  });

  it("rejects a non-positive limit before sending anything", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("synthetic-token");
    const fetchMock = stubFetch([productRoute]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("intake-product-header");
    await user.type(screen.getByLabelText("Название"), "X");
    await user.type(screen.getByLabelText("Лимит, USD"), "0");
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Лимит в USD должен быть положительным числом");
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit).method === "POST")).toBe(false);
  });
});
