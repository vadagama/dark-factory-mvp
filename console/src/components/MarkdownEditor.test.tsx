import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MarkdownEditor } from "./MarkdownEditor";
import { ApiClient } from "../api/client";
import type { ArtifactDocumentView } from "../api/types";
import {
  HEAD_REVISION,
  SPEC_CONTENT,
  SPEC_PATH,
  artifactDiff,
  artifactVersions,
  artifactWrite,
  specDocument,
  specDraft,
} from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";

const ENCODED = SPEC_PATH; // no characters that need escaping in the fixture path

function renderEditor(
  doc: ArtifactDocumentView,
  routes: FetchRoute[],
  options: { hasToken?: boolean; onSaved?: () => void; onReload?: () => void; onComment?: (anchor: string | null, text: string) => void } = {},
) {
  const fetchMock = stubFetch(routes);
  const api = new ApiClient({ tokenProvider: () => (options.hasToken === false ? null : "operator-token") });
  render(
    <MarkdownEditor
      changeId="chg_demo_001"
      document={doc}
      api={api}
      hasToken={options.hasToken ?? true}
      onTokenRequired={vi.fn()}
      onSaved={options.onSaved ?? vi.fn()}
      onReload={options.onReload ?? vi.fn()}
      onComment={options.onComment}
      autosaveDelayMs={20}
    />,
  );
  return fetchMock;
}

describe("MarkdownEditor (T090, ADR-035)", () => {
  it("keeps the content verbatim through Документ → Markdown → Чтение → Markdown (round-trip)", async () => {
    const user = userEvent.setup();
    const fetchMock = renderEditor(specDocument, []);
    // Document mode: rendered body + properties, unknown constructs are still in the source.
    expect(screen.getByTestId("editor-rendered")).toHaveTextContent("Health endpoint");
    expect(screen.getByTestId("editor-properties")).toHaveTextContent("REQ-001");

    await user.click(screen.getByTestId("editor-mode-markdown"));
    const textarea = screen.getByTestId("editor-textarea") as HTMLTextAreaElement;
    expect(textarea.value).toBe(SPEC_CONTENT);

    await user.click(screen.getByTestId("editor-mode-reading"));
    expect(screen.queryByTestId("editor-textarea")).toBeNull();
    expect(screen.getByTestId("editor-rendered")).toHaveTextContent("GET /health answers 503 while starting");

    await user.click(screen.getByTestId("editor-mode-markdown"));
    expect((screen.getByTestId("editor-textarea") as HTMLTextAreaElement).value).toBe(SPEC_CONTENT);
    // Unknown constructs survived and nothing was written anywhere.
    expect((screen.getByTestId("editor-textarea") as HTMLTextAreaElement).value).toContain(":::note");
    expect((screen.getByTestId("editor-textarea") as HTMLTextAreaElement).value).toContain("<!-- html comment kept verbatim -->");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByTestId("editor-save-state")).toHaveTextContent("Без изменений");
    expect(screen.getByTestId("editor-save-git")).toBeDisabled();
  });

  it("does not alter an edited text when switching modes either", async () => {
    const user = userEvent.setup();
    renderEditor(specDocument, [
      { method: "PUT", pattern: /artifact-drafts/, handler: () => jsonResponse(200, specDraft) },
    ]);
    await user.click(screen.getByTestId("editor-mode-markdown"));
    const textarea = screen.getByTestId("editor-textarea") as HTMLTextAreaElement;
    await user.click(textarea);
    await user.keyboard("{Control>}{End}{/Control}");
    await user.type(textarea, "\n::: custom-directive\nkept\n:::\n");
    const edited = textarea.value;
    expect(edited).not.toBe(SPEC_CONTENT);
    await user.click(screen.getByTestId("editor-mode-document"));
    await user.click(screen.getByTestId("editor-mode-reading"));
    await user.click(screen.getByTestId("editor-mode-markdown"));
    expect((screen.getByTestId("editor-textarea") as HTMLTextAreaElement).value).toBe(edited);
  });

  it("autosaves the draft with base_revision after typing and shows the save state", async () => {
    const user = userEvent.setup();
    const fetchMock = renderEditor(specDocument, [
      {
        method: "PUT",
        pattern: new RegExp(`/api/v1/changes/chg_demo_001/artifact-drafts/${ENCODED.replaceAll(".", "\\.")}$`),
        handler: (_url, init) => jsonResponse(200, { ...specDraft, content: bodyOf(init).content, updated_at: "2026-09-05T09:30:00Z" }),
      },
    ]);
    await user.click(screen.getByTestId("editor-mode-markdown"));
    const textarea = screen.getByTestId("editor-textarea");
    await user.type(textarea, "x");
    expect(screen.getByTestId("editor-save-state")).toHaveTextContent("Не сохранено");
    await waitFor(() => expect(screen.getByTestId("editor-save-state")).toHaveTextContent("Черновик сохранён 09:30"));
    const put = fetchMock.mock.calls.find(([, init]) => (init as RequestInit).method === "PUT") as [string, RequestInit];
    expect(put[0]).toContain("/artifact-drafts/.factory/changes/2026/CHG-0001-health/spec/requirements/REQ-001-health.md");
    expect((put[1].headers as Record<string, string>).Authorization).toBe("Bearer operator-token");
    expect(bodyOf(put[1])).toMatchObject({ base_revision: HEAD_REVISION });
    expect(String(bodyOf(put[1]).content)).toContain("x");
  });

  it("shows the autosave error honestly", async () => {
    const user = userEvent.setup();
    renderEditor(specDocument, [
      { method: "PUT", pattern: /artifact-drafts/, handler: () => jsonResponse(503, { type: "about:blank", title: "Service Unavailable", status: 503, detail: "draft store is down" }) },
    ]);
    await user.click(screen.getByTestId("editor-mode-markdown"));
    await user.type(screen.getByTestId("editor-textarea"), "y");
    await waitFor(() => expect(screen.getByTestId("editor-save-state")).toHaveTextContent("Не сохранено: draft store is down"));
  });

  it("offers to continue or discard an existing draft", async () => {
    const user = userEvent.setup();
    const fetchMock = renderEditor({ ...specDocument, draft: specDraft }, [
      { method: "DELETE", pattern: /artifact-drafts/, handler: () => new Response(null, { status: 204 }) },
    ]);
    expect(screen.getByTestId("editor-draft-banner")).toHaveTextContent("Есть несохранённый черновик");
    await user.click(screen.getByRole("button", { name: "Продолжить черновик" }));
    expect((screen.getByTestId("editor-textarea") as HTMLTextAreaElement).value).toBe(specDraft.content);
    expect(screen.getByTestId("editor-save-state")).toHaveTextContent("Черновик сохранён 09:30");
    expect(screen.queryByTestId("editor-draft-banner")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("discards the draft via DELETE and keeps the git content", async () => {
    const user = userEvent.setup();
    const fetchMock = renderEditor({ ...specDocument, draft: specDraft }, [
      { method: "DELETE", pattern: /artifact-drafts/, handler: () => new Response(null, { status: 204 }) },
    ]);
    await user.click(screen.getByRole("button", { name: "Отбросить" }));
    await waitFor(() => expect(screen.queryByTestId("editor-draft-banner")).toBeNull());
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit).method === "DELETE")).toBe(true);
    await user.click(screen.getByTestId("editor-mode-markdown"));
    expect((screen.getByTestId("editor-textarea") as HTMLTextAreaElement).value).toBe(SPEC_CONTENT);
  });

  it("saves to git with base_revision and the property edits; protected keys are read-only", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    const fetchMock = renderEditor(
      specDocument,
      [
        { method: "PUT", pattern: /artifact-drafts/, handler: () => jsonResponse(200, specDraft) },
        { method: "PUT", pattern: /\/artifacts\//, handler: () => jsonResponse(200, artifactWrite) },
      ],
      { onSaved },
    );
    // Protected keys have no input; editable ones do.
    expect(screen.queryByLabelText("id")).not.toBeInstanceOf(HTMLInputElement);
    expect(screen.getByTestId("editor-properties")).toHaveTextContent("защищено");
    const status = screen.getByLabelText("status") as HTMLInputElement;
    await user.clear(status);
    await user.type(status, "review");
    const priority = screen.getByLabelText("priority") as HTMLInputElement;
    await user.clear(priority);
    await user.type(priority, "1");
    await user.type(screen.getByLabelText("Сообщение коммита (необязательно)"), "spec: to review");
    await user.click(screen.getByTestId("editor-save-git"));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(artifactWrite));
    const put = fetchMock.mock.calls.find(([url]) => String(url).includes("/artifacts/")) as [string, RequestInit];
    expect(bodyOf(put[1])).toEqual({
      content: SPEC_CONTENT,
      base_revision: HEAD_REVISION,
      message: "spec: to review",
      properties: { status: "review", priority: 1 },
    });
  });

  it("on 409 shows the server detail and offers a reload — never overwrites", async () => {
    const user = userEvent.setup();
    const onReload = vi.fn();
    renderEditor(
      specDocument,
      [
        { method: "PUT", pattern: /artifact-drafts/, handler: () => jsonResponse(200, specDraft) },
        {
          method: "PUT",
          pattern: /\/artifacts\//,
          handler: () => jsonResponse(409, { type: "about:blank", title: "Conflict", status: 409, detail: "REQ-001-health.md changed after a1b2c3d4e5f6a7b8: head is 9999" }),
        },
      ],
      { onReload },
    );
    await user.click(screen.getByTestId("editor-mode-markdown"));
    await user.type(screen.getByTestId("editor-textarea"), "z");
    await user.click(screen.getByTestId("editor-save-git"));
    const conflict = await screen.findByTestId("editor-conflict");
    expect(conflict).toHaveTextContent("changed after a1b2c3d4e5f6a7b8: head is 9999");
    await user.click(screen.getByTestId("editor-reload"));
    expect(onReload).toHaveBeenCalled();
  });

  it("lists versions and renders the unified diff between two revisions", async () => {
    const user = userEvent.setup();
    renderEditor(specDocument, [
      { method: "GET", pattern: /artifact-versions/, handler: () => jsonResponse(200, artifactVersions) },
      {
        method: "GET",
        pattern: /artifact-diff/,
        handler: (url) => {
          expect(url.searchParams.get("from_revision")).toBe(artifactVersions[1].revision);
          expect(url.searchParams.get("to_revision")).toBe(artifactVersions[0].revision);
          return jsonResponse(200, artifactDiff);
        },
      },
    ]);
    await user.click(screen.getByTestId("editor-history-toggle"));
    const history = await screen.findByTestId("editor-history");
    await waitFor(() => expect(history).toHaveTextContent("spec: clarify AC-2"));
    expect(history).toHaveTextContent("spec: initial requirements");
    await user.click(screen.getByTestId("editor-show-diff"));
    const diff = await screen.findByTestId("editor-diff");
    expect(diff).toHaveTextContent("+1 / −1");
    expect(diff.querySelector("pre")?.textContent).toContain("-- AC-2: GET /health answers 500");
  });

  it("offers «Комментировать» for a text selection anchored to the id inside it", async () => {
    const user = userEvent.setup();
    const onComment = vi.fn();
    renderEditor(specDocument, [], { onComment });
    vi.spyOn(window, "getSelection").mockReturnValue({ toString: () => "AC-2: GET /health answers 503 while starting." } as unknown as Selection);
    await user.click(screen.getByTestId("editor-mode-reading"));
    await user.click(screen.getByTestId("editor-rendered"));
    expect(screen.getByTestId("editor-selection")).toHaveTextContent("якорь: AC-2");
    await user.click(screen.getByTestId("editor-comment-selection"));
    expect(onComment).toHaveBeenCalledWith("AC-2", "AC-2: GET /health answers 503 while starting.");
  });

  it("does not autosave without a token and says so", async () => {
    const user = userEvent.setup();
    const fetchMock = renderEditor(specDocument, [], { hasToken: false });
    await user.click(screen.getByTestId("editor-mode-markdown"));
    await user.type(screen.getByTestId("editor-textarea"), "q");
    expect(screen.getByTestId("editor-save-state")).toHaveTextContent("нужен токен оператора");
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
