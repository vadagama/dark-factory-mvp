import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { BudgetsPage, budgetShare } from "./BudgetsPage";
import { runCard, runCardSecond, runSummary, runSummarySecond } from "../test/fixtures";
import { jsonResponse, stubFetch } from "../test/mock-fetch";

describe("budgetShare", () => {
  it("is null for unset or non-positive budgets", () => {
    expect(budgetShare(10, null)).toBeNull();
    expect(budgetShare(10, 0)).toBeNull();
  });

  it("computes the percentage, capped at 100", () => {
    expect(budgetShare(50, 200)).toBe(25);
    expect(budgetShare(300, 200)).toBe(100);
  });
});

describe("BudgetsPage", () => {
  it("renders configured limits from the meta snapshot", async () => {
    stubFetch([
      { method: "GET", pattern: /\/api\/v1\/runs$/, handler: () => jsonResponse(200, [runSummary]) },
      {
        method: "GET",
        pattern: /\/api\/v1\/runs\/run_demo_001$/,
        handler: () => jsonResponse(200, runCard),
      },
    ]);
    render(
      <MemoryRouter>
        <BudgetsPage />
      </MemoryRouter>,
    );
    const limits = await screen.findByTestId("limits-table");
    expect(limits).toHaveTextContent("Rework");
    expect(limits).toHaveTextContent("3");
    // token/cost/deadline budgets are unset in the committed snapshot.
    expect(screen.getAllByText("не задан")).toHaveLength(3);
  });

  it("renders per-run usage against the limits", async () => {
    stubFetch([
      {
        method: "GET",
        pattern: /\/api\/v1\/runs$/,
        handler: () => jsonResponse(200, [runSummary, runSummarySecond]),
      },
      {
        method: "GET",
        pattern: /\/api\/v1\/runs\/run_demo_001$/,
        handler: () => jsonResponse(200, runCard),
      },
      {
        method: "GET",
        pattern: /\/api\/v1\/runs\/run_demo_002$/,
        handler: () => jsonResponse(200, runCardSecond),
      },
    ]);
    render(
      <MemoryRouter>
        <BudgetsPage />
      </MemoryRouter>,
    );
    const usage = await screen.findByTestId("usage-table");
    expect(usage).toHaveTextContent("run_demo_001");
    expect(usage).toHaveTextContent("run_demo_002");
    expect(usage).toHaveTextContent("$0.2847");
    expect(usage).toHaveTextContent("12 500 + 4 300");
    // No budgets configured → shares stay empty.
    expect(usage).toHaveTextContent("—");
    // run_demo_001 stages sum to 6 attempts (1+1+2+1+1), shown in the first row.
    const firstRow = usage.querySelector("tbody tr");
    expect(firstRow).toHaveTextContent("6");
  });
});
