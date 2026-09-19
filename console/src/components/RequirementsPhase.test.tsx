import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RequirementsPhase } from "./RequirementsPhase";
import type { RequirementsPhaseProps } from "./RequirementsPhase";
import { ApiClient } from "../api/client";
import {
  artifactTree,
  artifactTreeNoBranch,
  assumptionChoice,
  assumptionText,
  intentDocument,
  questionAnswered,
  questionChoice,
  questionText,
  reworkOrderDone,
  reworkOrderPending,
  specDocument,
} from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";

function renderPhase(routes: FetchRoute[], overrides: Partial<RequirementsPhaseProps> = {}) {
  const fetchMock = stubFetch(routes);
  const api = new ApiClient({ tokenProvider: () => "operator-token" });
  const onChanged = vi.fn();
  render(
    <RequirementsPhase
      changeId="chg_demo_001"
      api={api}
      hasToken
      onTokenRequired={vi.fn()}
      tree={artifactTree}
      treeError={null}
      documents={[specDocument, intentDocument]}
      questions={[questionChoice, questionText, assumptionText, assumptionChoice, questionAnswered]}
      orders={[reworkOrderDone, reworkOrderPending]}
      onChanged={onChanged}
      onOpenArtifact={vi.fn()}
      onSelectFragment={vi.fn()}
      {...overrides}
    />,
  );
  return { fetchMock, onChanged };
}

const answerRoute = (questionId: string): FetchRoute => ({
  method: "POST",
  pattern: new RegExp(`/api/v1/changes/chg_demo_001/questions/${questionId}/answer$`),
  handler: (_url, init) =>
    jsonResponse(200, {
      ...questionChoice,
      id: questionId,
      status: "answered",
      answer: { value: bodyOf(init).value, comment: null, answered_by: "operator@example", answered_at: "2026-09-05T10:00:00Z" },
    }),
});

describe("RequirementsPhase (T089)", () => {
  it("lists the spec delta with stable ids, counts and the view mark", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged } = renderPhase([
      { method: "POST", pattern: /artifact-views/, handler: () => jsonResponse(200, { ...specDocument, viewed: true }) },
    ]);
    const delta = screen.getByTestId("requirements-delta");
    expect(delta).toHaveTextContent("REQ-001-health.md");
    expect(delta).toHaveTextContent("intent.md");
    // Only spec artifacts: the ADR is not part of the requirements delta.
    expect(delta).not.toHaveTextContent("ADR-001-probe.md");
    expect(screen.getByTestId("anchors-REQ-001-health.md")).toHaveTextContent("REQ-001");
    expect(screen.getByTestId("anchors-REQ-001-health.md")).toHaveTextContent("AC-2");
    expect(screen.getByTestId("delta-REQ-001-health.md")).toHaveTextContent("вопросов: 1 · замечаний: 1");
    // intent.md is already viewed; the spec is not — the button marks the view, never approves.
    expect(screen.getByTestId("delta-intent.md")).toHaveTextContent("просмотрено");
    await user.click(screen.getByTestId("view-REQ-001-health.md"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const post = fetchMock.mock.calls.find(([url]) => String(url).includes("/artifact-views/")) as [string, RequestInit];
    expect(bodyOf(post[1])).toEqual({ revision: specDocument.revision });
    expect(delta).not.toHaveTextContent(/согласован/i);
  });

  it("answers a choice question in one click with the option as the value", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged } = renderPhase([answerRoute("q_choice_001")]);
    const card = screen.getByTestId("question-q_choice_001");
    expect(card).toHaveTextContent("блокирует гейт");
    await user.click(screen.getByTestId("options-q_choice_001").querySelector("button")!);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const post = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(post[0]).toContain("/questions/q_choice_001/answer");
    expect((post[1].headers as Record<string, string>).Authorization).toBe("Bearer operator-token");
    expect(bodyOf(post[1])).toEqual({ value: "503" });
  });

  it("answers a number question through the small input and «Ответить»", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPhase([answerRoute("q_text_002")]);
    const card = screen.getByTestId("question-q_text_002");
    const input = card.querySelector("input")!;
    await user.type(input, "5");
    await user.click(card.querySelector('button[type="submit"]')!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(bodyOf((fetchMock.mock.calls[0] as [string, RequestInit])[1])).toEqual({ value: "5" });
  });

  it("renders assumptions as non-blocking cards: confirm sends \"confirmed\", fix sends free text, choice shows options", async () => {
    const user = userEvent.setup();
    const { fetchMock } = renderPhase([answerRoute("q_assume_003"), answerRoute("q_assume_004")]);
    const assumptions = screen.getByTestId("requirements-assumptions");
    expect(assumptions).toHaveTextContent("Полагаю, что /health не требует аутентификации.");
    expect(screen.getByTestId("requirements-questions")).not.toHaveTextContent("не требует аутентификации");

    const text = screen.getByTestId("question-q_assume_003");
    await user.click(text.querySelector("button.button--primary")!); // «Подтвердить»
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(bodyOf((fetchMock.mock.calls[0] as [string, RequestInit])[1])).toEqual({ value: "confirmed" });

    const choice = screen.getByTestId("question-q_assume_004");
    expect(choice.querySelector("button.button--primary")).toBeNull(); // no literal "confirmed" for a choice
    expect(screen.queryByTestId("options-q_assume_004")).toBeNull();
    await user.click(screen.getAllByRole("button", { name: "Варианты" })[0]);
    await user.click(screen.getByTestId("options-q_assume_004").querySelector("button")!);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(bodyOf((fetchMock.mock.calls[1] as [string, RequestInit])[1])).toEqual({ value: "JSON" });

    // «Исправить» opens the free-text input on the text assumption.
    await user.click(text.querySelector('button.button:not(.button--primary)')!);
    expect(text.querySelector("input")).not.toBeNull();
  });

  it("shows the server detail when an answer is refused", async () => {
    const user = userEvent.setup();
    renderPhase([
      {
        method: "POST",
        pattern: /answer$/,
        handler: () => jsonResponse(422, { type: "about:blank", title: "Unprocessable", status: 422, detail: "answer '7' is not one of the options: 503, 500" }),
      },
    ]);
    await user.click(screen.getByTestId("options-q_choice_001").querySelector("button")!);
    await screen.findByText(/answer '7' is not one of the options/);
  });

  it("shows the agent summary «что изменил / что осталось» and the pending order state", () => {
    renderPhase([]);
    const done = screen.getByTestId("summary-rw_done_001");
    expect(done).toHaveTextContent("Что изменил");
    expect(done).toHaveTextContent("AC-2: код при старте 500 → 503");
    expect(done).toHaveTextContent("Что осталось");
    expect(done).toHaveTextContent("Таймаут проверки зависимостей не задан");
    expect(done).toHaveTextContent("cmt_addressed_003");
    expect(screen.getByTestId("rework-rw_pending_002")).toHaveTextContent("Раунд ещё не запущен");
  });

  it("is honest when there is no branch yet and when the contour has no repository", () => {
    renderPhase([], { tree: artifactTreeNoBranch, documents: [] });
    expect(screen.getByTestId("requirements-no-branch")).toHaveTextContent("Ветки изменения ещё нет");
  });

  it("shows the 503 detail instead of fake artifacts", () => {
    renderPhase([], { tree: null, documents: [], treeError: "the product repository is not configured in this contour" });
    expect(screen.getByTestId("requirements-delta")).toHaveTextContent("the product repository is not configured in this contour");
    expect(screen.queryByTestId("anchors-REQ-001-health.md")).toBeNull();
  });
});
