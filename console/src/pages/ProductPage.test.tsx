import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ProductPage, findNewChangeAction } from "./ProductPage";
import * as token from "../api/token";
import {
  changeWithBrief,
  product,
  productCreated,
  productGuidanceCreated,
  productGuidanceReady,
  productValidated,
} from "../test/fixtures";
import { jsonResponse, stubFetch } from "../test/mock-fetch";
import type { Guidance, Product } from "../api/types";

function renderPage(productId = "prd_demo_001"): void {
  render(
    <MemoryRouter initialEntries={[`/products/${productId}`]}>
      <Routes>
        <Route path="/products/:productId" element={<ProductPage />} />
        <Route path="/products/:productId/new-change" element={<p data-testid="intake-marker">intake</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function readRoutes(current: Product, guidance: Guidance) {
  return [
    { method: "GET", pattern: new RegExp(`/api/v1/products/${current.id}$`), handler: () => jsonResponse(200, current) },
    {
      method: "GET",
      pattern: new RegExp(`/api/v1/products/${current.id}/guidance$`),
      handler: () => jsonResponse(200, guidance),
    },
    {
      method: "GET",
      pattern: /\/api\/v1\/changes$/,
      handler: (url: URL) =>
        jsonResponse(200, url.searchParams.get("product_id") === "prd_demo_001" ? [changeWithBrief] : []),
    },
  ];
}

describe("findNewChangeAction", () => {
  it("finds POST /changes among primary and secondary, null otherwise", () => {
    expect(findNewChangeAction(productGuidanceReady)?.label).toBe("Новая фича");
    expect(findNewChangeAction(productGuidanceCreated)).toBeNull();
    expect(findNewChangeAction(null)).toBeNull();
  });
});

describe("ProductPage", () => {
  it("renders the header, the next step and the stacked sections", async () => {
    stubFetch(readRoutes(product, productGuidanceReady));
    renderPage();
    const header = await screen.findByTestId("product-header");
    expect(header).toHaveTextContent("Demo service");
    expect(header).toHaveTextContent("ready");
    expect(header).toHaveTextContent("github/acme/demo-service");

    expect(screen.getByTestId("next-step")).toHaveTextContent("Продукт готов к работе");
    expect(screen.getByTestId("next-step-primary")).toHaveTextContent("Новая фича");

    expect(screen.getByTestId("product-overview")).toHaveTextContent("baseline/2026-09");
    const changes = screen.getByTestId("product-changes-table");
    expect(changes).toHaveTextContent("Add /health endpoint");
    expect(changes).toHaveTextContent("только спецификации");
    expect(changes).toHaveTextContent("complete");
    expect(changes).toHaveTextContent("$25.0000");
    expect(changes).toHaveTextContent("150 000 токенов");
    expect(screen.getByRole("link", { name: "Add /health endpoint" })).toHaveAttribute("href", "/changes/chg_demo_001");

    expect(screen.getByTestId("product-baseline")).toHaveTextContent("baseline/2026-09");
    expect(screen.getByText("Просмотр документов baseline появится в M2.")).toBeInTheDocument();
    expect(screen.getByTestId("product-deliveries")).toHaveTextContent("не задан");
    expect(screen.getByText("История доставок появится в M5.")).toBeInTheDocument();
    expect(screen.getByTestId("new-change-button")).toHaveAttribute("href", "/products/prd_demo_001/new-change");
  });

  it("navigates to the intake when the guidance primary is «Новая фича»", async () => {
    stubFetch(readRoutes(product, productGuidanceReady));
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("next-step");
    await user.click(screen.getByTestId("next-step-primary"));
    await screen.findByTestId("intake-marker");
  });

  it("validates the repository from the next step and shows the observed state", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    let loads = 0;
    const fetchMock = stubFetch([
      {
        method: "GET",
        pattern: /\/api\/v1\/products\/prd_demo_002$/,
        handler: () => {
          loads += 1;
          return jsonResponse(200, loads >= 2 ? { ...productCreated, status: "ready", state_revision: 2 } : productCreated);
        },
      },
      {
        method: "GET",
        pattern: /\/api\/v1\/products\/prd_demo_002\/guidance$/,
        handler: () => jsonResponse(200, productGuidanceCreated),
      },
      { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, []) },
      {
        method: "POST",
        pattern: /\/api\/v1\/products\/prd_demo_002\/validate$/,
        handler: () => jsonResponse(200, { ...productValidated, id: "prd_demo_002" }),
      },
    ]);
    const user = userEvent.setup();
    renderPage("prd_demo_002");
    await screen.findByTestId("next-step");
    expect(screen.getByTestId("next-step-blockers")).toHaveTextContent("снимает: оператор");
    await user.click(screen.getByTestId("next-step-primary"));

    const notice = await screen.findByText(/Репозиторий проверен/);
    expect(notice).toHaveTextContent("baseline current");
    expect(notice).toHaveTextContent("ветка main");
    // The page re-reads the product after the validation.
    await waitFor(() => expect(screen.getByTestId("product-header")).toHaveTextContent("ready"));
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit).method === "POST") as [string, RequestInit];
    expect((post[1].headers as Record<string, string>).Authorization).toBe("Bearer operator-token");
  });

  it("surfaces the 503 detail when provisioning is not configured", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    stubFetch([
      ...readRoutes(productCreated, productGuidanceCreated),
      {
        method: "POST",
        pattern: /\/api\/v1\/products\/prd_demo_002\/validate$/,
        handler: () =>
          jsonResponse(503, {
            type: "about:blank",
            title: "Service Unavailable",
            status: 503,
            detail: "Repository provisioning is not configured on this contour",
          }),
      },
    ]);
    const user = userEvent.setup();
    renderPage("prd_demo_002");
    await screen.findByTestId("product-header");
    await user.click(screen.getByTestId("validate-button"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Repository provisioning is not configured on this contour");
  });

  it("is fail-closed: validating without a token opens the token dialog", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    const fetchMock = stubFetch(readRoutes(productCreated, productGuidanceCreated));
    const user = userEvent.setup();
    renderPage("prd_demo_002");
    await screen.findByTestId("product-header");
    expect(screen.getByTestId("product-token-hint")).toHaveTextContent("требует токен оператора");
    await user.click(screen.getByTestId("validate-button"));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Нужен токен оператора");
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit).method === "POST")).toBe(false);
  });

  it("renders «Новая фича» disabled with the reason when the guidance says so", async () => {
    const guidance: Guidance = {
      ...productGuidanceReady,
      primary: { ...productGuidanceReady.primary, enabled: false, reason: "Продукт заблокирован оператором." },
    };
    stubFetch(readRoutes(product, guidance));
    renderPage();
    await screen.findByTestId("product-header");
    expect(screen.getByTestId("new-change-button")).toBeDisabled();
    expect(screen.getByTestId("product-header")).toHaveTextContent("Продукт заблокирован оператором.");
  });
});
