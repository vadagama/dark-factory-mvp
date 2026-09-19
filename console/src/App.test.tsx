import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { jsonResponse, stubFetch } from "./test/mock-fetch";

function renderAt(path: string): void {
  stubFetch([
    { method: "GET", pattern: /\/api\/v1\/products$/, handler: () => jsonResponse(200, []) },
    { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, []) },
    { method: "GET", pattern: /\/api\/v1\/runs$/, handler: () => jsonResponse(200, []) },
    { method: "GET", pattern: /\/api\/v1\/ci\/stages$/, handler: () => jsonResponse(200, { schema_version: 1, repository: null, available: false, reason: "stub", stages: [] }) },
  ]);
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App routing (ADR-037 IA)", () => {
  it("has the four top-level areas and products as the index", async () => {
    renderAt("/");
    const nav = screen.getByRole("navigation", { name: "Основная навигация" });
    expect(nav).toHaveTextContent("Продукты");
    expect(nav).toHaveTextContent("Требует внимания");
    expect(nav).toHaveTextContent("Активность");
    expect(nav).toHaveTextContent("Служебное");
    await screen.findByText(/Продуктов пока нет/);
  });

  it("renders honest placeholders for attention and activity", () => {
    renderAt("/attention");
    expect(screen.getByRole("status")).toHaveTextContent("Inbox «Требует внимания» появится в M5 (T116).");
  });

  it("renders the activity placeholder", () => {
    renderAt("/activity");
    expect(screen.getByRole("status")).toHaveTextContent("Лента активности вне объёма MVP.");
  });

  it("redirects the old service paths to /service/*", async () => {
    renderAt("/budgets");
    await screen.findByTestId("limits-table");
    expect(screen.getByRole("link", { name: "Служебное" })).toHaveClass("active");
  });

  it("redirects /settings to /service/settings", () => {
    renderAt("/settings");
    expect(screen.getByTestId("token-absent")).toBeInTheDocument();
  });

  it("lists the service screens on the service landing", () => {
    renderAt("/service");
    const links = screen.getByTestId("service-links");
    expect(links.querySelector('a[href="/service/budgets"]')).not.toBeNull();
    expect(links.querySelector('a[href="/service/ci"]')).not.toBeNull();
    expect(links.querySelector('a[href="/service/settings"]')).not.toBeNull();
  });

  it("falls back to the products page for unknown paths", async () => {
    renderAt("/nope/nothing");
    await screen.findByText(/Продуктов пока нет/);
  });
});
