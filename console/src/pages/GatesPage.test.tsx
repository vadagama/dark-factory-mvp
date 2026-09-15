import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { GatesPage } from "./GatesPage";
import * as token from "../api/token";
import {
  blockerFinding,
  changeCard,
  decision,
  gateResult,
  makeChangeCard,
} from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/changes/chg_demo_001/gates"]}>
      <Routes>
        <Route path="/changes/:changeId/gates" element={<GatesPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const READ_ROUTES = (cardResponse: () => Response = () => jsonResponse(200, changeCard)) => [
  {
    method: "GET",
    pattern: /\/api\/v1\/changes\/chg_demo_001\/approvals$/,
    handler: () => jsonResponse(200, [decision]),
  },
  {
    method: "GET",
    pattern: /\/api\/v1\/changes\/chg_demo_001$/,
    handler: cardResponse,
  },
  {
    method: "GET",
    pattern: /\/api\/v1\/runs\/run_demo_001\/gates$/,
    handler: () => jsonResponse(200, [gateResult]),
  },
  {
    method: "GET",
    pattern: /\/api\/v1\/runs\/run_demo_001\/findings$/,
    handler: () => jsonResponse(200, [blockerFinding]),
  },
];

describe("GatesPage", () => {
  it("renders gate results, open blockers and the approvals history", async () => {
    stubFetch(READ_ROUTES());
    renderPage();
    const gates = await screen.findByTestId("gates-run_demo_001");
    expect(gates).toHaveTextContent("review");
    expect(gates).toHaveTextContent("passed");
    expect(gates).toHaveTextContent("abc1234def");
    const blockers = screen.getByTestId("blockers-run_demo_001");
    expect(blockers).toHaveTextContent("fnd_001");
    expect(blockers).toHaveTextContent("Fix failing integration tests");
    const approvals = screen.getByTestId("approvals-table");
    expect(approvals).toHaveTextContent("review");
    expect(approvals).toHaveTextContent("approved");
    expect(approvals).toHaveTextContent("human");
  });

  it("keeps the approval form closed without a token", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    stubFetch(READ_ROUTES());
    renderPage();
    await screen.findByTestId("gates-run_demo_001");
    expect(screen.getByTestId("approval-token-hint")).toHaveTextContent("Запись решения недоступна");
    expect(screen.getByRole("button", { name: "Записать решение" })).toBeDisabled();
  });

  it("records an approval, retries once on 409 and reuses the Idempotency-Key", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    let cardLoads = 0;
    let approvalPosts = 0;
    const idemKeys: string[] = [];
    const bodies: Record<string, unknown>[] = [];
    const fetchMock = stubFetch([
      ...READ_ROUTES(() => {
        cardLoads += 1;
        // First load: no decisions yet; the 409 re-read sees one recorded decision.
        return jsonResponse(200, makeChangeCard(cardLoads >= 2 ? 1 : 0));
      }),
      {
        method: "POST",
        pattern: /\/api\/v1\/changes\/chg_demo_001\/approvals$/,
        handler: (_url, init) => {
          approvalPosts += 1;
          idemKeys.push((init.headers as Record<string, string>)["Idempotency-Key"]);
          bodies.push(bodyOf(init));
          if (approvalPosts === 1) {
            return jsonResponse(409, {
              type: "about:blank",
              title: "Conflict",
              status: 409,
              detail: "state_revision mismatch",
            });
          }
          return jsonResponse(201, decision);
        },
      },
    ]);

    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("gates-run_demo_001");
    await user.type(screen.getByLabelText("subject_revision"), "abc1234def");
    await user.click(screen.getByRole("button", { name: "Записать решение" }));

    const notice = await screen.findByText(/Решение записано: dec_abc123/);
    expect(notice).toHaveTextContent("после перечитывания карточки");
    expect(approvalPosts).toBe(2);
    expect(idemKeys[0]).toBe(idemKeys[1]);
    expect(bodies[0].expected_state_revision).toBe(1);
    expect(bodies[1].expected_state_revision).toBe(2);
    expect(bodies[1].subject_revision).toBe("abc1234def");
    expect(bodies[1].gate).toBe("review");
    expect(bodies[1].outcome).toBe("approved");
    // No read carries the token: only the two POSTs have Authorization.
    const readWithAuth = fetchMock.mock.calls.filter(([, init]) => {
      const headers = (init as RequestInit).headers as Record<string, string> | undefined;
      const method = ((init as RequestInit).method ?? "GET").toUpperCase();
      return method === "GET" && headers !== undefined && headers.Authorization !== undefined;
    });
    expect(readWithAuth).toHaveLength(0);
  });

  it("refreshes expected_state_revision when the reloaded card reports the new decision", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    let cardLoads = 0;
    stubFetch([
      ...READ_ROUTES(() => {
        cardLoads += 1;
        // The reload after the recorded decision sees the fresh count.
        return jsonResponse(200, makeChangeCard(cardLoads >= 2 ? 1 : 0));
      }),
      {
        method: "POST",
        pattern: /\/api\/v1\/changes\/chg_demo_001\/approvals$/,
        handler: () => jsonResponse(201, decision),
      },
    ]);

    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("gates-run_demo_001");
    const field = screen.getByLabelText("expected_state_revision") as HTMLInputElement;
    expect(field.value).toBe("1");

    await user.type(screen.getByLabelText("subject_revision"), "abc1234def");
    await user.click(screen.getByRole("button", { name: "Записать решение" }));
    await screen.findByText(/Решение записано: dec_abc123/);

    // decisions_count 0 → 1 refreshes the optimistic lock without remounting the
    // form, so the success notice survives the reload.
    await waitFor(() => expect(field.value).toBe("2"));
    expect(screen.getByText(/Решение записано: dec_abc123/)).toBeInTheDocument();

    // The field stays editable: an operator edit is not reset on re-render.
    await user.clear(field);
    await user.type(field, "9");
    expect(field.value).toBe("9");
  });
});
