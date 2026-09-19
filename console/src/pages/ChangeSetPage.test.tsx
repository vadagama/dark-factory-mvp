import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { ChangeSetPage } from "./ChangeSetPage";
import * as token from "../api/token";
import type { Guidance, Phase, PhaseGate } from "../api/types";
import {
  HEAD_REVISION,
  artifactTree,
  assumptionText,
  changeGuidanceRequirementsClosed,
  changeGuidanceRequirementsOpen,
  changeGuidanceStart,
  changeWithBrief,
  commentDetached,
  commentOpen,
  decision,
  intentDocument,
  makeChangeCard,
  phaseGateOf,
  phaseGateOpen,
  questionChoice,
  reworkOrderDone,
  runCard,
  specDocument,
} from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/changes/chg_demo_001"]}>
      <Routes>
        <Route path="/changes/:changeId" element={<ChangeSetPage />} />
        <Route path="/changes/:changeId/card" element={<p>legacy card</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

function workspaceRoutes(guidance: Guidance, gateOverride?: (phase: Phase) => PhaseGate): FetchRoute[] {
  return [
    { method: "GET", pattern: /\/api\/v1\/changes\/chg_demo_001$/, handler: () => jsonResponse(200, makeChangeCard(1, changeWithBrief)) },
    { method: "GET", pattern: /\/changes\/chg_demo_001\/guidance$/, handler: () => jsonResponse(200, guidance) },
    { method: "GET", pattern: /\/runs\/run_demo_001$/, handler: () => jsonResponse(200, runCard) },
    { method: "GET", pattern: /\/changes\/chg_demo_001\/approvals$/, handler: () => jsonResponse(200, [decision]) },
    { method: "GET", pattern: /\/changes\/chg_demo_001\/questions$/, handler: () => jsonResponse(200, [questionChoice, assumptionText]) },
    { method: "GET", pattern: /\/changes\/chg_demo_001\/comments$/, handler: () => jsonResponse(200, [commentOpen, commentDetached]) },
    { method: "GET", pattern: /\/changes\/chg_demo_001\/rework-orders$/, handler: () => jsonResponse(200, [reworkOrderDone]) },
    {
      method: "GET",
      pattern: /\/changes\/chg_demo_001\/phase-gate$/,
      handler: (url) => {
        const phase = url.searchParams.get("phase") as Phase;
        return jsonResponse(200, gateOverride ? gateOverride(phase) : phaseGateOf(phase));
      },
    },
    { method: "GET", pattern: /\/changes\/chg_demo_001\/artifacts$/, handler: () => jsonResponse(200, artifactTree) },
    { method: "GET", pattern: /\/artifacts\/.*intent\.md$/, handler: () => jsonResponse(200, intentDocument) },
    { method: "GET", pattern: /\/artifacts\/.*REQ-001-health\.md$/, handler: () => jsonResponse(200, specDocument) },
  ];
}

describe("ChangeSetPage (T088, ADR-037 p.4)", () => {
  it("renders the shell: top panel, phases with counts, tabs, context panel, one CTA — and no percentage", async () => {
    stubFetch(workspaceRoutes(changeGuidanceRequirementsClosed));
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });

    const top = screen.getByTestId("workspace-top");
    expect(top).toHaveTextContent("chg_demo_001");
    expect(screen.getByTestId("workspace-phase")).toHaveTextContent("Требования");
    await waitFor(() => expect(screen.getByTestId("workspace-budget")).toHaveTextContent("факт $0.2847 · лимит 25.0000 USD · прогноз —"));
    expect(screen.getByTestId("workspace-blockers")).toHaveTextContent("1");
    expect(screen.getByTestId("change-intake-line")).toHaveTextContent("сценарий: только спецификации");

    const phases = screen.getByTestId("workspace-phases");
    for (const label of ["Ф0 Инициатива", "Ф1 Требования", "Ф2 Архитектура", "Ф3 Интерфейс", "Ф4 План", "Ф5 Исполнение", "Ф6 Демонстрация", "Ф7 Доставка"]) {
      expect(phases).toHaveTextContent(label);
    }
    await waitFor(() =>
      expect(screen.getByTestId("phase-requirements")).toHaveTextContent("вопросов 4 · замечаний 2 · итерация 1"),
    );
    expect(screen.getByTestId("phase-requirements")).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId("phase-requirements")).toHaveTextContent("в работе");
    expect(screen.getByTestId("phase-initiative")).toHaveTextContent("пройдена");
    expect(screen.getByTestId("phase-plan")).toHaveTextContent("не начата");

    const tabs = screen.getByRole("tablist");
    expect(tabs).toHaveTextContent("Результат");
    expect(tabs).toHaveTextContent("Изменения");
    expect(tabs).toHaveTextContent("Проверки");
    expect(tabs).toHaveTextContent("История");
    expect(screen.getByTestId("workspace-context")).toBeInTheDocument();
    expect(screen.getByTestId("context-panel")).toBeInTheDocument();

    const decisionPanel = screen.getByTestId("workspace-decision");
    expect(within(decisionPanel).getAllByTestId("next-step-primary")).toHaveLength(1);
    expect(screen.getByTestId("next-step-primary")).toHaveTextContent("Ответить на вопросы");

    // The requirements delta is on screen; the ADR placeholder is not.
    await screen.findByTestId("anchors-REQ-001-health.md");
    expect(document.body.textContent).not.toMatch(/\d+\s?%/);
  });

  it("collapses the context panel and switches the tabs", async () => {
    const user = userEvent.setup();
    stubFetch(workspaceRoutes(changeGuidanceRequirementsClosed));
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });
    await user.click(screen.getByTestId("context-toggle"));
    expect(screen.queryByTestId("context-panel")).toBeNull();
    await user.click(screen.getByTestId("context-toggle"));
    expect(screen.getByTestId("context-panel")).toBeInTheDocument();

    await user.click(screen.getByTestId("tab-checks"));
    await waitFor(() => expect(screen.getByTestId("phase-gate")).toHaveTextContent("закрыт"));
    expect(screen.getByTestId("phase-gate-approvals")).toHaveTextContent("неактуально");
    expect(screen.getByTestId("phase-gate-counts")).toHaveTextContent("1 из 3");

    await user.click(screen.getByTestId("tab-changes"));
    await waitFor(() => expect(screen.getByTestId("artifacts-table")).toHaveTextContent("ADR-001-probe.md"));

    await user.click(screen.getByTestId("tab-history"));
    expect(screen.getByTestId("history-decisions")).toHaveTextContent("dec_abc123");
    expect(screen.getByTestId("history-tab")).toHaveTextContent("rw_done_001");
  });

  it("shows honest placeholders for the phases of later milestones", async () => {
    const user = userEvent.setup();
    stubFetch(workspaceRoutes(changeGuidanceRequirementsClosed));
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });
    await user.click(screen.getByTestId("phase-architecture"));
    expect(screen.getByTestId("phase-placeholder")).toHaveTextContent("появится в M3");
    await user.click(screen.getByTestId("phase-execution"));
    expect(screen.getByTestId("phase-placeholder")).toHaveTextContent("появится в M4");
    await user.click(screen.getByTestId("phase-delivery"));
    expect(screen.getByTestId("phase-placeholder")).toHaveTextContent("появится в M5");
  });

  it("shows the brief in the initiative phase and focuses it from the guidance", async () => {
    const user = userEvent.setup();
    stubFetch(workspaceRoutes(changeGuidanceStart));
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });
    expect(screen.getByTestId("phase-initiative")).toHaveAttribute("aria-current", "true");
    await screen.findByTestId("brief-section");
    expect(screen.getByTestId("next-step-cli")).toHaveTextContent("factory run advance --change-id chg_demo_001");
    // The secondary «Дополнить бриф» is not a button, but the phase shows the editor anyway.
    await user.click(screen.getByTestId("phase-requirements"));
    expect(screen.queryByTestId("brief-section")).toBeNull();
    await user.click(screen.getByTestId("phase-initiative"));
    expect(screen.getByTestId("brief-section")).toBeInTheDocument();
  });

  it("records the phase approval from the decision panel, version-bound to the gate revision", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    const user = userEvent.setup();
    const fetchMock = stubFetch([
      ...workspaceRoutes(changeGuidanceRequirementsOpen, (phase) => (phase === "requirements" ? phaseGateOpen : phaseGateOf(phase))),
      {
        method: "POST",
        pattern: /\/changes\/chg_demo_001\/approvals$/,
        handler: () => jsonResponse(201, { ...decision, id: "dec_new", gate: "specification", commit_sha: HEAD_REVISION }),
      },
    ]);
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });
    expect(screen.getByTestId("next-step-primary")).toHaveTextContent("Согласовать требования");
    await user.click(screen.getByTestId("next-step-primary"));
    const form = await screen.findByTestId("approval-form");
    expect(screen.getByTestId("approval-revision")).toHaveTextContent(HEAD_REVISION.slice(0, 12));
    await user.type(screen.getByTestId("approval-comment"), "требования понятны");
    await user.click(within(form).getByTestId("approval-submit"));
    await waitFor(() => expect(screen.getByTestId("approval-recorded")).toHaveTextContent("Решение записано: dec_new"));
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit).method === "POST") as [string, RequestInit];
    expect(bodyOf(post[1])).toEqual({
      gate: "specification",
      outcome: "approved",
      subject_revision: HEAD_REVISION,
      comment: "требования понятны",
      expected_state_revision: 2,
    });
  });

  it("shows the 409 detail of a stale approval and offers a reload", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    const user = userEvent.setup();
    stubFetch([
      ...workspaceRoutes(changeGuidanceRequirementsOpen, (phase) => (phase === "requirements" ? phaseGateOpen : phaseGateOf(phase))),
      {
        method: "POST",
        pattern: /\/changes\/chg_demo_001\/approvals$/,
        handler: () => jsonResponse(409, { type: "about:blank", title: "Conflict", status: 409, detail: "subject_revision a1b2 is not the current revision ffee: reload and decide on what you see" }),
      },
    ]);
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });
    await user.click(screen.getByTestId("next-step-primary"));
    await user.click(await screen.findByTestId("approval-submit"));
    await screen.findByText(/reload and decide on what you see/);
    expect(screen.getByTestId("approval-reload")).toBeInTheDocument();
  });

  it("opens the rework form from the guidance secondary path and the editor from the delta", async () => {
    const user = userEvent.setup();
    stubFetch(workspaceRoutes(changeGuidanceRequirementsClosed));
    renderPage();
    await screen.findByTestId("anchors-REQ-001-health.md");
    await user.click(screen.getByTestId("rework-toggle"));
    expect(screen.getByTestId("rework-form")).toBeInTheDocument();
    await user.click(within(screen.getByTestId("delta-REQ-001-health.md")).getByRole("button", { name: "REQ-001-health.md" }));
    await screen.findByTestId("markdown-editor");
    expect(screen.getByTestId("artifact-editor-pane")).toHaveTextContent("REQ-001-health.md");
    await user.click(screen.getByTestId("editor-back"));
    expect(screen.queryByTestId("markdown-editor")).toBeNull();
  });

  it("is honest when the contour has no repository (503 on artifacts)", async () => {
    stubFetch([
      ...workspaceRoutes(changeGuidanceRequirementsClosed).filter((route) => !/artifacts/.test(route.pattern.source)),
      {
        method: "GET",
        pattern: /\/artifacts/,
        handler: () => jsonResponse(503, { type: "about:blank", title: "Service Unavailable", status: 503, detail: "the product repository is not configured in this contour" }),
      },
    ]);
    renderPage();
    await screen.findByRole("heading", { name: "Add /health endpoint" });
    await waitFor(() => expect(screen.getByTestId("requirements-delta")).toHaveTextContent("the product repository is not configured in this contour"));
    expect(screen.queryByTestId("anchors-REQ-001-health.md")).toBeNull();
  });
});
