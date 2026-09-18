import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ChangesListPage } from "./ChangesListPage";
import * as token from "../api/token";
import { change, runSummary, runSummarySecond } from "../test/fixtures";
import { jsonResponse, stubFetch } from "../test/mock-fetch";

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <ChangesListPage />
    </MemoryRouter>,
  );
}

describe("ChangesListPage", () => {
  it("renders rows with risk and last-run status badges and card links", async () => {
    stubFetch([
      { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, [change]) },
      {
        method: "GET",
        pattern: /\/api\/v1\/runs$/,
        handler: () => jsonResponse(200, [runSummary, runSummarySecond]),
      },
    ]);
    renderPage();
    const table = await screen.findByTestId("changes-table");
    expect(table).toHaveTextContent("chg_demo_001");
    expect(table).toHaveTextContent("Add /health endpoint");
    expect(table).toHaveTextContent("github/acme/demo-service");
    // Status of the change's own run wins; runs of other changes are ignored.
    expect(table).toHaveTextContent("succeeded");
    const link = screen.getByRole("link", { name: "chg_demo_001" });
    expect(link).toHaveAttribute("href", "/changes/chg_demo_001");
  });

  it("hides intake behind a token (fail-closed, FR-001)", async () => {
    stubFetch([
      { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, []) },
      { method: "GET", pattern: /\/api\/v1\/runs$/, handler: () => jsonResponse(200, []) },
    ]);
    vi.spyOn(token, "getToken").mockReturnValue(null);
    renderPage();
    await screen.findByText("Пока нет изменений — создайте первое через форму приёмки.");
    expect(screen.getByTestId("intake-token-hint")).toHaveTextContent("Мутации недоступны");
    expect(screen.getByRole("button", { name: "Создать изменение" })).toBeDisabled();
  });

  it("shows an RFC 7807 error detail on API failure", async () => {
    stubFetch([
      {
        method: "GET",
        pattern: /\/api\/v1\/changes$/,
        handler: () =>
          jsonResponse(500, {
            type: "about:blank",
            title: "Internal Server Error",
            status: 500,
            detail: "Store unavailable",
          }),
      },
      { method: "GET", pattern: /\/api\/v1\/runs$/, handler: () => jsonResponse(200, []) },
    ]);
    renderPage();
    const error = await screen.findByRole("alert");
    expect(error).toHaveTextContent("Store unavailable");
  });

  it("shows a freshness stamp after a successful load (T-095)", async () => {
    stubFetch([
      { method: "GET", pattern: /\/api\/v1\/changes$/, handler: () => jsonResponse(200, [change]) },
      {
        method: "GET",
        pattern: /\/api\/v1\/runs$/,
        handler: () => jsonResponse(200, [runSummary, runSummarySecond]),
      },
    ]);
    renderPage();
    const stamp = await screen.findByTestId("updated-at");
    expect(stamp).toHaveTextContent(/обновлено \d{2}:\d{2}:\d{2}/);
  });
});
