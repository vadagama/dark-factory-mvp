import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArchitecturePhase } from "./ArchitecturePhase";
import type { ArchitecturePhaseProps } from "./ArchitecturePhase";
import { MermaidBlock } from "./MermaidBlock";
import { ApiClient } from "../api/client";
import {
  ADR_001_PATH,
  ADR_002_PATH,
  DESIGN_OVERVIEW_PATH,
  alternativeOrderDone,
  alternativeOrderPending,
  artifactTreeDesign,
  artifactTreeNoBranch,
  commentOpen,
  decisionsView,
  designOverviewDocument,
} from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { FetchRoute } from "../test/mock-fetch";

// jsdom cannot lay out SVG: the diagram engine is replaced by a stub that
// renders a marker for a valid diagram and throws for a broken one.
vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async (_id: string, source: string) => {
      if (source.includes("broken")) {
        throw new Error("Parse error on line 2: unexpected token");
      }
      return { svg: `<svg role="img"><text>${source.split("\n")[0]}</text></svg>` };
    }),
  },
}));

const decisionsRoute: FetchRoute = { method: "GET", pattern: /\/changes\/chg_demo_001\/decisions$/, handler: () => jsonResponse(200, decisionsView) };

function renderPhase(routes: FetchRoute[], overrides: Partial<ArchitecturePhaseProps> = {}) {
  const fetchMock = stubFetch([...routes, decisionsRoute]);
  const api = new ApiClient({ tokenProvider: () => "operator-token" });
  const onChanged = vi.fn();
  const onOpenArtifact = vi.fn();
  render(
    <ArchitecturePhase
      changeId="chg_demo_001"
      api={api}
      hasToken
      onTokenRequired={vi.fn()}
      tree={artifactTreeDesign}
      treeError={null}
      overview={designOverviewDocument}
      comments={[{ ...commentOpen, phase: "architecture", anchor: { artifact: ADR_002_PATH, anchor_id: null, revision: null } }]}
      orders={[alternativeOrderPending, alternativeOrderDone]}
      reloadKey={0}
      onChanged={onChanged}
      onOpenArtifact={onOpenArtifact}
      {...overrides}
    />,
  );
  return { fetchMock, onChanged, onOpenArtifact };
}

describe("ArchitecturePhase (T095/T093)", () => {
  it("renders the design overview with its mermaid diagram and opens it in the editor", async () => {
    const user = userEvent.setup();
    const { onOpenArtifact } = renderPhase([]);
    const overview = screen.getByTestId("design-overview");
    expect(overview).toHaveTextContent("Проверка живости живёт в отдельном модуле");
    // The fence is rendered lazily; the SVG replaces the source once mermaid resolves.
    await screen.findByTestId("mermaid-rendered");
    expect(screen.getByTestId("design-overview-body").querySelector("svg")).not.toBeNull();
    await user.click(screen.getByTestId("open-overview"));
    expect(onOpenArtifact).toHaveBeenCalledWith(DESIGN_OVERVIEW_PATH);
  });

  it("renders every decision card with status, sections, alternatives, impact and the ADR link", async () => {
    const user = userEvent.setup();
    const { onOpenArtifact } = renderPhase([]);
    const first = await screen.findByTestId("decision-adr:prd_demo_001:0001");
    expect(first).toHaveTextContent("Probe as a separate module");
    expect(first).toHaveTextContent("предложено");
    expect(first).toHaveTextContent("Предложение");
    expect(first).toHaveTextContent("Обоснование");
    expect(first).toHaveTextContent("Последствия");
    expect(screen.getByTestId("alternatives-adr:prd_demo_001:0001")).toHaveTextContent("новая зависимость запрещена брифом");
    expect(screen.getByTestId("impact-adr:prd_demo_001:0001")).toHaveTextContent("public_api");
    // The finished alternative order shows its summary and the affected artifacts open the editor.
    expect(screen.getByTestId("revised-adr:prd_demo_001:0001")).toHaveTextContent("ADR-001: probe реализуется без внешней библиотеки");
    await user.click(within(screen.getByTestId("affected-adr:prd_demo_001:0001")).getByRole("button", { name: "overview.md" }));
    expect(onOpenArtifact).toHaveBeenCalledWith(DESIGN_OVERVIEW_PATH);
    await user.click(screen.getByTestId("open-adr-adr:prd_demo_001:0001"));
    expect(onOpenArtifact).toHaveBeenCalledWith(ADR_001_PATH);

    const second = screen.getByTestId("decision-adr:prd_demo_001:0002");
    expect(second).toHaveTextContent("требует пересмотра");
    expect(screen.getByTestId("pending-adr:prd_demo_001:0002")).toHaveTextContent("ожидает раунда");
    expect(screen.queryByTestId("alternative-toggle-adr:prd_demo_001:0002")).toBeNull();
    // Parse errors are a warning, never hidden; the revision line says the phase is not approved.
    expect(screen.getByText(/Не разобраны/)).toHaveTextContent("ADR-003-broken.md");
    expect(screen.getByTestId("decisions-revision")).toHaveTextContent("фаза не согласована");
    expect(document.body.textContent).not.toMatch(/\d+\s?%/);
  });

  it("requests an alternative inline: instruction + selected comments → POST, then the parent reloads", async () => {
    const user = userEvent.setup();
    const { fetchMock, onChanged } = renderPhase([
      {
        method: "POST",
        pattern: /\/decisions\/adr%3Aprd_demo_001%3A0001\/alternative$/,
        handler: () => jsonResponse(201, { ...alternativeOrderPending, id: "rw_alt_new", decision_ids: ["adr:prd_demo_001:0001"] }),
      },
    ]);
    await screen.findByTestId("decision-adr:prd_demo_001:0001");
    await user.click(screen.getByTestId("alternative-toggle-adr:prd_demo_001:0001"));
    const form = screen.getByTestId("alternative-form-adr:prd_demo_001:0001");
    expect(within(form).getByTestId("alternative-submit-adr:prd_demo_001:0001")).toBeDisabled();
    await user.type(screen.getByTestId("alternative-instruction-adr:prd_demo_001:0001"), "Рассмотреть middleware вместо модуля.");
    await user.click(within(form).getByRole("checkbox"));
    await user.click(screen.getByTestId("alternative-submit-adr:prd_demo_001:0001"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit).method === "POST") as [string, RequestInit];
    expect(post[0]).toContain("/decisions/adr%3Aprd_demo_001%3A0001/alternative");
    expect((post[1].headers as Record<string, string>).Authorization).toBe("Bearer operator-token");
    expect(bodyOf(post[1])).toEqual({ instruction: "Рассмотреть middleware вместо модуля.", comment_ids: ["cmt_open_001"] });
    expect(screen.getByTestId("decision-adr:prd_demo_001:0001")).toHaveTextContent("Поручение rw_alt_new создано");
    // Still on the same screen: the decisions overview is there.
    expect(screen.getByTestId("decisions-overview")).toBeInTheDocument();
  });

  it("shows the 409 server text when another order of the phase is pending", async () => {
    const user = userEvent.setup();
    renderPhase([
      {
        method: "POST",
        pattern: /\/alternative$/,
        handler: () => jsonResponse(409, { type: "about:blank", title: "Conflict", status: 409, detail: "rework order 'rw_alt_003' of phase architecture is pending" }),
      },
    ]);
    await screen.findByTestId("decision-adr:prd_demo_001:0001");
    await user.click(screen.getByTestId("alternative-toggle-adr:prd_demo_001:0001"));
    await user.type(screen.getByTestId("alternative-instruction-adr:prd_demo_001:0001"), "x");
    await user.click(screen.getByTestId("alternative-submit-adr:prd_demo_001:0001"));
    await screen.findByText(/rework order 'rw_alt_003' of phase architecture is pending/);
    // The form stays open for a correction.
    expect(screen.getByTestId("alternative-form-adr:prd_demo_001:0001")).toBeInTheDocument();
  });

  it("is honest without a branch, without an overview and when the contour has no repository", async () => {
    renderPhase([], { tree: artifactTreeNoBranch, overview: null });
    expect(screen.getByTestId("architecture-no-branch")).toHaveTextContent("Ветки изменения ещё нет");
    await screen.findByTestId("decisions-overview");
  });

  it("shows the 503 detail instead of fake artifacts", () => {
    renderPhase([], { tree: null, overview: null, treeError: "the product repository is not configured in this contour" });
    expect(screen.getByTestId("design-overview")).toHaveTextContent("the product repository is not configured in this contour");
  });

  it("names the branch when design/overview.md is missing", () => {
    renderPhase([], { overview: null });
    expect(screen.getByTestId("architecture-no-overview")).toHaveTextContent("design/overview.md");
    expect(screen.getByTestId("architecture-no-overview")).toHaveTextContent("артефактов проектирования: 3");
  });
});

describe("MermaidBlock", () => {
  it("falls back to the source with a visible note when the diagram cannot be rendered — never an empty box", async () => {
    render(<MermaidBlock source={"flowchart LR\n  broken -->"} />);
    expect(screen.getByTestId("mermaid-pending")).toHaveTextContent("flowchart LR");
    const failed = await screen.findByTestId("mermaid-failed");
    expect(failed).toHaveTextContent("Схема не отрисована: Parse error on line 2: unexpected token");
    expect(failed.querySelector("pre")).toHaveTextContent("broken -->");
  });
});
