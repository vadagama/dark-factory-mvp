import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ProductsPage, countChangesByProduct } from "./ProductsPage";
import * as token from "../api/token";
import { change, changeWithBrief, product, productCreated } from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<ProductsPage />} />
        <Route path="/products/:productId" element={<p data-testid="product-page-marker">product page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("countChangesByProduct", () => {
  it("counts per product and skips changes without a product", () => {
    const counts = countChangesByProduct([change, changeWithBrief, { ...change, id: "x", product_id: null }]);
    expect(counts.get("prd_demo_001")).toBe(2);
    expect(counts.size).toBe(1);
  });
});

describe("ProductsPage", () => {
  it("renders products with status, repository, change count and a link to the product page", async () => {
    stubFetch([
      { method: "GET", pattern: /\/api\/v1\/products$/, handler: () => jsonResponse(200, [product, productCreated]) },
      { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, [change, changeWithBrief]) },
    ]);
    renderPage();
    const table = await screen.findByTestId("products-table");
    expect(table).toHaveTextContent("Demo service");
    expect(table).toHaveTextContent("github/acme/demo-service");
    expect(table).toHaveTextContent("ready");
    expect(table).toHaveTextContent("created");
    expect(screen.getByTestId("product-row-prd_demo_001")).toHaveTextContent("2");
    expect(screen.getByTestId("product-row-prd_demo_002")).toHaveTextContent("0");
    expect(screen.getByRole("link", { name: "Demo service" })).toHaveAttribute("href", "/products/prd_demo_001");
  });

  it("shows an honest empty state and a fail-closed form without a token", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    stubFetch([
      { method: "GET", pattern: /\/api\/v1\/products$/, handler: () => jsonResponse(200, []) },
      { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, []) },
    ]);
    renderPage();
    await screen.findByText(/Продуктов пока нет — зарегистрируйте первый продукт/);
    expect(screen.getByTestId("product-token-hint")).toHaveTextContent("Мутации недоступны");
    expect(screen.getByRole("button", { name: "Добавить продукт" })).toBeDisabled();
  });

  it("registers a product with Bearer auth and navigates to its page", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("synthetic-token");
    const fetchMock = stubFetch([
      { method: "GET", pattern: /\/api\/v1\/products$/, handler: () => jsonResponse(200, []) },
      { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, []) },
      {
        method: "POST",
        pattern: /\/api\/v1\/products$/,
        handler: (_url, init) =>
          jsonResponse(201, { ...product, ...bodyOf(init), status: "created", state_revision: 1 }),
      },
    ]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/Продуктов пока нет/);
    const idField = screen.getByLabelText("ID") as HTMLInputElement;
    expect(idField.value).toMatch(/^prd_[0-9a-f]{12}$/);
    await user.clear(idField);
    await user.type(idField, "prd_custom_1");
    await user.type(screen.getByLabelText("Название"), "Billing");
    await user.selectOptions(screen.getByLabelText("Провайдер"), "gitlab");
    await user.type(screen.getByLabelText("Репозиторий (slug)"), "acme/billing");
    await user.type(screen.getByLabelText("URL репозитория (необязательно)"), "https://gitlab.com/acme/billing");
    await user.click(screen.getByRole("button", { name: "Добавить продукт" }));

    await screen.findByTestId("product-page-marker");
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit).method === "POST") as [string, RequestInit];
    expect(post[0]).toContain("/api/v1/products");
    const headers = post[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer synthetic-token");
    expect(headers["Idempotency-Key"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(bodyOf(post[1])).toEqual({
      id: "prd_custom_1",
      name: "Billing",
      repository: { provider: "gitlab", slug: "acme/billing" },
      description: null,
      repository_url: "https://gitlab.com/acme/billing",
      baseline_ref: null,
      dev_env_ref: null,
    });
  });
});
