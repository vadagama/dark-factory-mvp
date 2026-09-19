import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ChangeCardPage } from "./ChangeCardPage";
import * as token from "../api/token";
import {
  changeGuidanceDraft,
  changeGuidanceStart,
  changeTrace,
  changeWithBrief,
  evidenceItem,
  makeChangeCard,
  runCard,
} from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";
import type { ChangeCard, Guidance } from "../api/types";

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/changes/chg_demo_001"]}>
      <Routes>
        <Route path="/changes/:changeId" element={<ChangeCardPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function readRoutes(card: () => ChangeCard, guidance: Guidance): FetchRoute[] {
  return [
    { method: "GET", pattern: /\/api\/v1\/changes\/chg_demo_001$/, handler: () => jsonResponse(200, card()) },
    { method: "GET", pattern: /\/api\/v1\/changes\/chg_demo_001\/guidance$/, handler: () => jsonResponse(200, guidance) },
    { method: "GET", pattern: /\/api\/v1\/changes\/chg_demo_001\/trace$/, handler: () => jsonResponse(200, changeTrace) },
    { method: "GET", pattern: /\/api\/v1\/runs\/run_demo_001$/, handler: () => jsonResponse(200, runCard) },
    { method: "GET", pattern: /\/api\/v1\/runs\/run_demo_001\/evidence$/, handler: () => jsonResponse(200, [evidenceItem]) },
    { method: "GET", pattern: /\/api\/v1\/runs\/run_demo_001\/findings$/, handler: () => jsonResponse(200, []) },
  ];
}

describe("ChangeCardPage", () => {
  it("renders the next step, the intake line and the brief next to the card", async () => {
    stubFetch(readRoutes(() => makeChangeCard(1, changeWithBrief), changeGuidanceStart));
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });
    expect(screen.getByTestId("next-step")).toHaveTextContent("Бриф готов — можно запускать фазу «Требования»");
    expect(screen.getByTestId("next-step-cli")).toHaveTextContent("factory run advance --change-id chg_demo_001");

    const line = screen.getByTestId("change-intake-line");
    expect(line).toHaveTextContent("сценарий: только спецификации");
    expect(line).toHaveTextContent("лимит: 25.0000 USD, 150 000 токенов");
    expect(screen.getByRole("link", { name: "продукт: prd_demo_001" })).toHaveAttribute("href", "/products/prd_demo_001");

    const brief = screen.getByTestId("brief-section");
    expect(brief).toHaveTextContent("complete");
    expect(brief).toHaveTextContent("сформулировал: агент");
    expect(screen.getByTestId("brief-problem")).toHaveTextContent("Нет проверки живости сервиса");
    expect(screen.getByTestId("brief-goal")).toHaveTextContent("Эндпоинт /health отвечает 200 при готовности");
    expect(brief).toHaveTextContent("Без новых зависимостей");
    expect(brief).toHaveTextContent("Метрики readiness");
    expect(screen.getByTestId("brief-source")).toHaveTextContent("Нужен health endpoint для демо-сервиса");
    // Everything that was there before is still there.
    expect(screen.getByTestId("stages-run_demo_001")).toHaveTextContent("specification");
    expect(screen.getByTestId("evidence-run_demo_001")).toHaveTextContent("ev_001");
  });

  it("keeps the brief editor closed without a token", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    stubFetch(readRoutes(() => makeChangeCard(1, changeWithBrief), changeGuidanceStart));
    renderPage();
    await screen.findByTestId("brief-form");
    expect(screen.getByTestId("brief-token-hint")).toHaveTextContent("Сохранение недоступно");
    expect(screen.getByRole("button", { name: "Сохранить бриф" })).toBeDisabled();
  });

  it("focuses the brief editor from the guidance primary and saves an operator-edited brief via PUT", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    const draft = { ...changeWithBrief, brief: { ...changeWithBrief.brief!, goal: null, status: "draft" as const, formulated_by: "agent" as const } };
    let saved = false;
    const fetchMock = stubFetch([
      ...readRoutes(() => makeChangeCard(1, saved ? changeWithBrief : draft), changeGuidanceDraft),
      {
        method: "PUT",
        pattern: /\/api\/v1\/changes\/chg_demo_001\/brief$/,
        handler: (_url, init) => {
          saved = true;
          return jsonResponse(200, { ...changeWithBrief, brief: bodyOf(init) });
        },
      },
    ]);
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("brief-form");
    expect(screen.getByTestId("brief-section")).toHaveTextContent("draft");
    expect(screen.getByTestId("brief-goal")).toHaveTextContent("не заполнена");

    await user.click(screen.getByTestId("next-step-primary"));
    const problem = screen.getByLabelText("Проблема");
    expect(problem).toHaveFocus();

    await user.type(screen.getByLabelText("Цель"), "Эндпоинт /health отвечает 200 при готовности");
    await user.click(screen.getByRole("button", { name: "Сохранить бриф" }));
    await screen.findByText("Бриф сохранён.");

    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit).method === "PUT") as [string, RequestInit];
    expect((put[1].headers as Record<string, string>).Authorization).toBe("Bearer operator-token");
    expect(bodyOf(put[1])).toEqual({
      problem: "Нет проверки живости сервиса",
      goal: "Эндпоинт /health отвечает 200 при готовности",
      constraints: ["Без новых зависимостей"],
      out_of_scope: ["Метрики readiness"],
      source_text: "Нужен health endpoint для демо-сервиса",
      status: "complete",
      // Edited by hand: the agent is no longer the author.
      formulated_by: "operator",
      error: null,
    });
    // The card is re-read after the save and shows the completed brief.
    await waitFor(() => expect(screen.getByTestId("brief-goal")).toHaveTextContent("отвечает 200"));
  });
});
