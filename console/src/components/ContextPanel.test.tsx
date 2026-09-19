import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ContextPanel } from "./ContextPanel";
import type { ContextPanelProps } from "./ContextPanel";
import { ApiClient } from "../api/client";
import {
  HEAD_REVISION,
  SPEC_PATH,
  artifactTree,
  commentAddressed,
  commentDetached,
  commentOpen,
  questionAnswered,
  questionChoice,
  reworkOrderDone,
  reworkOrderPending,
  specDocument,
} from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";

function renderPanel(routes: FetchRoute[], overrides: Partial<ContextPanelProps> = {}) {
  const fetchMock = stubFetch(routes);
  const api = new ApiClient({ tokenProvider: () => "operator-token" });
  const onChanged = vi.fn();
  const onReworkOpenChange = vi.fn();
  render(
    <ContextPanel
      changeId="chg_demo_001"
      api={api}
      phase="requirements"
      hasToken
      onTokenRequired={vi.fn()}
      tree={artifactTree}
      documents={[specDocument]}
      comments={[commentOpen, commentDetached, commentAddressed]}
      questions={[questionChoice, questionAnswered]}
      orders={[reworkOrderDone]}
      fragment={null}
      onFragmentChange={vi.fn()}
      seed={null}
      reworkOpen={false}
      onReworkOpenChange={onReworkOpenChange}
      onChanged={onChanged}
      {...overrides}
    />,
  );
  return { fetchMock, onChanged, onReworkOpenChange };
}

describe("ContextPanel (T088/T089, ADR-034)", () => {
  it("shows a detached anchor explicitly and the agent's «исправлено» as not closed", () => {
    renderPanel([]);
    const detached = screen.getByTestId("comment-cmt_detached_002");
    expect(detached).toHaveTextContent("якорь потерян");
    expect(screen.getByTestId("detached-cmt_detached_002")).toHaveTextContent("не переносится на другой элемент");
    expect(screen.getByTestId("comment-cmt_open_001")).not.toHaveTextContent("якорь потерян");
    const addressed = screen.getByTestId("comment-cmt_addressed_003");
    expect(addressed).toHaveTextContent("исправлено агентом");
    expect(addressed).toHaveTextContent("Заменил код на 503 в AC-2.");
    expect(screen.getByTestId("reopen-cmt_addressed_003")).toBeInTheDocument();
    expect(screen.getByTestId("close-cmt_addressed_003")).toBeInTheDocument();
  });

  it("filters the comments by the selected fragment", () => {
    renderPanel([], { fragment: { artifact: SPEC_PATH, anchorId: "AC-1" } });
    expect(screen.getByTestId("context-fragment")).toHaveTextContent("AC-1");
    expect(screen.getByTestId("comment-cmt_open_001")).toBeInTheDocument();
    expect(screen.queryByTestId("comment-cmt_detached_002")).toBeNull();
  });

  it("posts a comment anchored to the chosen fragment and reminds that rework is explicit", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged } = renderPanel([
      { method: "POST", pattern: /\/comments$/, handler: (_url, init) => jsonResponse(201, { ...commentOpen, id: "cmt_new", body: bodyOf(init).body }) },
    ]);
    await user.selectOptions(screen.getByTestId("comment-artifact"), SPEC_PATH);
    await user.selectOptions(screen.getByTestId("comment-anchor"), "AC-2");
    await user.type(screen.getByTestId("comment-body"), "Уточнить код при старте");
    await user.click(screen.getByTestId("comment-submit"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const post = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(post[0]).toMatch(/\/api\/v1\/changes\/chg_demo_001\/comments$/);
    expect(bodyOf(post[1])).toEqual({
      artifact: SPEC_PATH,
      anchor_id: "AC-2",
      revision: HEAD_REVISION,
      body: "Уточнить код при старте",
      phase: "requirements",
    });
    expect(screen.getByText(/Замечание cmt_new записано/)).toHaveTextContent("Доработка не запускается сама");
  });

  it("prefills the composer from the editor seed with the quoted selection", () => {
    renderPanel([], { seed: { id: 1, artifact: SPEC_PATH, anchorId: "AC-1", quote: "GET /health answers 200" } });
    expect((screen.getByTestId("comment-artifact") as HTMLSelectElement).value).toBe(SPEC_PATH);
    expect((screen.getByTestId("comment-anchor") as HTMLSelectElement).value).toBe("AC-1");
    expect((screen.getByTestId("comment-body") as HTMLTextAreaElement).value).toContain("> GET /health answers 200");
  });

  it("closes and reopens a comment through the operator endpoints", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged } = renderPanel([
      { method: "POST", pattern: /\/close$/, handler: () => jsonResponse(200, { ...commentOpen, status: "closed" }) },
      { method: "POST", pattern: /\/reopen$/, handler: () => jsonResponse(200, { ...commentAddressed, status: "open" }) },
    ]);
    await user.click(screen.getByTestId("close-cmt_open_001"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect((fetchMock.mock.calls[0] as [string])[0]).toContain("/comments/cmt_open_001/close");
    await user.click(screen.getByTestId("reopen-cmt_addressed_003"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
    expect((fetchMock.mock.calls[1] as [string])[0]).toContain("/comments/cmt_addressed_003/reopen");
    // The agent's «addressed» is never offered as an operator button.
    expect(screen.queryByRole("button", { name: /исправлено/i })).toBeNull();
  });

  it("sends the rework order with the selected comments, answered questions, instruction and the head revision", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged, onReworkOpenChange } = renderPanel(
      [{ method: "POST", pattern: /rework-orders$/, handler: () => jsonResponse(201, reworkOrderPending) }],
      { reworkOpen: true },
    );
    const form = screen.getByTestId("rework-form");
    // Open comments are preselected, addressed ones are not; answered questions are preselected.
    const checkboxes = form.querySelectorAll('input[type="checkbox"]');
    expect(checkboxes.length).toBe(4); // 3 non-closed comments + 1 answered question
    await user.type(screen.getByTestId("rework-instruction"), "Учесть соединение с БД.");
    await user.click(screen.getByTestId("rework-submit"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(onReworkOpenChange).toHaveBeenCalledWith(false);
    const post = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(bodyOf(post[1])).toEqual({
      phase: "requirements",
      comment_ids: ["cmt_open_001", "cmt_detached_002"],
      question_ids: ["q_answered_005"],
      instruction: "Учесть соединение с БД.",
      expected_revision: HEAD_REVISION,
    });
  });

  it("shows the 409 detail when a round is already pending", async () => {
    const user = userEvent.setup();
    renderPanel(
      [
        {
          method: "POST",
          pattern: /rework-orders$/,
          handler: () => jsonResponse(409, { type: "about:blank", title: "Conflict", status: 409, detail: "rework order 'rw_pending_002' of phase requirements is pending" }),
        },
      ],
      { reworkOpen: true },
    );
    await user.click(screen.getByTestId("rework-submit"));
    await screen.findByText(/rework order 'rw_pending_002' of phase requirements is pending/);
  });
});
