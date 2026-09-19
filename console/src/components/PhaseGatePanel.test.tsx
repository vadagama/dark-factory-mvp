import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PhaseGatePanel } from "./PhaseGatePanel";
import type { PhaseGatePanelProps } from "./PhaseGatePanel";
import { ApiClient } from "../api/client";
import type { PhaseGate } from "../api/types";
import { HEAD_REVISION, decision, phaseGateClosed, phaseGateInterface, phaseGateInterfaceBackendOnly } from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";

function renderPanel(gate: PhaseGate | null, routes: FetchRoute[] = [], overrides: Partial<PhaseGatePanelProps> = {}) {
  const fetchMock = stubFetch(routes);
  const api = new ApiClient({ tokenProvider: () => (overrides.hasToken === false ? null : "operator-token") });
  const onChanged = vi.fn();
  render(<PhaseGatePanel gate={gate} changeId="chg_demo_001" api={api} hasToken decisionsCount={1} onChanged={onChanged} {...overrides} />);
  return { fetchMock, onChanged };
}

describe("PhaseGatePanel (T087/T088, M3 T097)", () => {
  it("renders the gate, its phase, the reasons, counts and stale approvals without a green status", () => {
    renderPanel(phaseGateClosed);
    const panel = screen.getByTestId("phase-gate");
    expect(panel).toHaveTextContent("specification");
    expect(panel).toHaveTextContent("фаза: Требования");
    expect(panel).toHaveTextContent("закрыт");
    expect(screen.getByTestId("phase-gate-reasons")).toHaveTextContent("Блокирующих вопросов без ответа: 2");
    expect(screen.getByTestId("phase-gate-counts")).toHaveTextContent("1 из 3");
    expect(screen.getByTestId("phase-gate-approvals")).toHaveTextContent("неактуально");
    expect(panel).not.toHaveTextContent("согласовано на текущей ревизии");
    // No checks and no UI requirement for a non-UI phase: the sections are absent, not faked.
    expect(screen.queryByTestId("phase-gate-checks")).toBeNull();
    expect(screen.queryByTestId("phase-gate-ui-requirement")).toBeNull();
    expect(document.body.textContent).not.toMatch(/\d+\s?%/);
  });

  it("shows planned checks as «запланировано на исполнении» — never in the success tone", () => {
    renderPanel(phaseGateInterface);
    const checks = screen.getByTestId("phase-gate-checks");
    expect(screen.getByTestId("check-axe")).toHaveTextContent("запланировано на исполнении");
    expect(screen.getByTestId("check-visual_regression")).toHaveTextContent("запланировано на исполнении");
    expect(checks.querySelectorAll(".badge--success")).toHaveLength(0);
    expect(checks.querySelectorAll(".badge--warning")).toHaveLength(2);
    expect(screen.getByTestId("phase-gate-ui-requirement")).toHaveTextContent("UI требуется");
    expect(screen.getByTestId("phase-gate-ui-requirement")).toHaveTextContent("источник: архитектор");
    expect(screen.queryByTestId("waive-ui")).toBeNull();
  });

  it("backend-only: shows «Не требуется» with the reason and posts the waiver on gate ui / phase interface", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged } = renderPanel(phaseGateInterfaceBackendOnly, [
      {
        method: "POST",
        pattern: /\/changes\/chg_demo_001\/approvals$/,
        handler: () => jsonResponse(201, { ...decision, id: "dec_waived", gate: "ui", outcome: "waived", phase: "interface", commit_sha: HEAD_REVISION }),
      },
    ]);
    const requirement = screen.getByTestId("phase-gate-ui-not-required");
    expect(requirement).toHaveTextContent("Не требуется");
    expect(requirement).toHaveTextContent("UI не требуется: Изменение только серверное: /health не имеет экрана");
    expect(requirement).toHaveTextContent("источник: архитектор");
    expect(screen.getByTestId("check-axe")).toHaveTextContent("не требуется");
    expect(screen.getByTestId("phase-gate-checks").querySelectorAll(".badge--success")).toHaveLength(0);

    await user.click(screen.getByTestId("waive-ui"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const post = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(post[0]).toMatch(/\/api\/v1\/changes\/chg_demo_001\/approvals$/);
    expect((post[1].headers as Record<string, string>).Authorization).toBe("Bearer operator-token");
    expect(bodyOf(post[1])).toEqual({
      gate: "ui",
      phase: "interface",
      outcome: "waived",
      subject_revision: HEAD_REVISION,
      comment: "Изменение только серверное: /health не имеет экрана",
      expected_state_revision: 2,
    });
  });

  it("waives without a revision: the button stays enabled and subject_revision is omitted (M3: a waiver is about the phase)", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged } = renderPanel({ ...phaseGateInterfaceBackendOnly, current_revision: null }, [
      {
        method: "POST",
        pattern: /\/changes\/chg_demo_001\/approvals$/,
        handler: () => jsonResponse(201, { ...decision, id: "dec_waived", gate: "ui", outcome: "waived", phase: "interface", commit_sha: null }),
      },
    ]);
    expect(screen.getByTestId("waive-ui")).toBeEnabled();
    expect(screen.getByTestId("phase-gate-ui-requirement")).toHaveTextContent("без привязки к документу");
    await user.click(screen.getByTestId("waive-ui"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const body = bodyOf((fetchMock.mock.calls[0] as [string, RequestInit])[1]);
    expect(body).toEqual({
      gate: "ui",
      phase: "interface",
      outcome: "waived",
      comment: "Изменение только серверное: /health не имеет экрана",
      expected_state_revision: 2,
    });
    expect("subject_revision" in body).toBe(false);
  });

  it("treats an unbound waiver as in effect", () => {
    renderPanel({
      ...phaseGateInterfaceBackendOnly,
      current_revision: null,
      approvals: [{ decision_id: "dec_w", outcome: "waived", revision: null, state: "unbound", comment: "серверное", phase: "interface" }],
    });
    expect(screen.queryByTestId("waive-ui")).toBeNull();
    expect(screen.getByTestId("phase-gate")).toHaveTextContent("Пропуск подтверждён оператором.");
    expect(screen.getByTestId("phase-gate")).toHaveTextContent("пропущена с основанием");
  });

  it("disables the waiver without a token and says the waiver is recorded when a current waived decision exists", () => {
    renderPanel(phaseGateInterfaceBackendOnly, [], { hasToken: false });
    expect(screen.getByTestId("waive-ui")).toBeDisabled();
    expect(screen.getByTestId("phase-gate-ui-requirement")).toHaveTextContent("требует токен оператора");
  });

  it("shows the server detail when the waiver is refused", async () => {
    const user = userEvent.setup();
    renderPanel(phaseGateInterfaceBackendOnly, [
      {
        method: "POST",
        pattern: /approvals$/,
        handler: () => jsonResponse(409, { type: "about:blank", title: "Conflict", status: 409, detail: "subject_revision is not the current revision" }),
      },
    ]);
    await user.click(screen.getByTestId("waive-ui"));
    await screen.findByText(/Пропуск не записан: subject_revision is not the current revision/);
  });

  it("reports the recorded waiver instead of the button", () => {
    renderPanel({
      ...phaseGateInterfaceBackendOnly,
      approvals: [{ decision_id: "dec_w", outcome: "waived", revision: HEAD_REVISION, state: "current", comment: "серверное", phase: "interface" }],
    });
    expect(screen.queryByTestId("waive-ui")).toBeNull();
    expect(screen.getByTestId("phase-gate")).toHaveTextContent("Пропуск подтверждён оператором на текущей ревизии");
    expect(screen.getByTestId("phase-gate-approvals")).toHaveTextContent("Интерфейс");
  });
});
