import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { InterfacePhase } from "./InterfacePhase";
import type { InterfacePhaseProps } from "./InterfacePhase";
import { ApiClient } from "../api/client";
import type { UiSpecView } from "../api/types";
import {
  SCENARIO_PATH,
  SCREEN_STATUS_PATH,
  artifactTree,
  artifactTreeNoBranch,
  commentElementDetached,
  commentElementOpen,
  uiSpecView,
} from "../test/fixtures";
import { jsonResponse, stubFetch } from "../test/mock-fetch";

function renderPhase(spec: UiSpecView | null = uiSpecView, overrides: Partial<InterfacePhaseProps> = {}) {
  stubFetch(
    spec
      ? [{ method: "GET", pattern: /\/changes\/chg_demo_001\/ui$/, handler: () => jsonResponse(200, spec) }]
      : [
          {
            method: "GET",
            pattern: /\/ui$/,
            handler: () => jsonResponse(503, { type: "about:blank", title: "Service Unavailable", status: 503, detail: "the product repository is not configured in this contour" }),
          },
        ],
  );
  const api = new ApiClient({ tokenProvider: () => "operator-token" });
  const onOpenArtifact = vi.fn();
  const onSelectFragment = vi.fn();
  render(
    <InterfacePhase
      changeId="chg_demo_001"
      api={api}
      tree={artifactTree}
      treeError={null}
      comments={[commentElementDetached, commentElementOpen]}
      reloadKey={0}
      onOpenArtifact={onOpenArtifact}
      onSelectFragment={onSelectFragment}
      {...overrides}
    />,
  );
  return { onOpenArtifact, onSelectFragment };
}

describe("InterfacePhase (T096)", () => {
  it("opens on «Сценарии» by default: cards with steps linking to their screens", async () => {
    const user = userEvent.setup();
    const { onOpenArtifact } = renderPhase();
    expect(screen.getByTestId("ui-tab-scenarios")).toHaveAttribute("aria-selected", "true");
    const scenario = await screen.findByTestId("scenario-SCN-001");
    expect(scenario).toHaveTextContent("Оператор проверяет живость");
    expect(screen.getByTestId("step-SCN-001-S1")).toHaveTextContent("Открыть страницу статуса");
    expect(screen.getByTestId("step-SCN-001-S3")).not.toHaveTextContent("SCR-");
    await user.click(within(scenario).getByRole("button", { name: "SCN-001-check-health.md" }));
    expect(onOpenArtifact).toHaveBeenCalledWith(SCENARIO_PATH);
    // A step's screen chip switches to the gallery and focuses that screen.
    await user.click(within(screen.getByTestId("step-SCN-001-S2")).getByRole("button", { name: "SCR-002" }));
    expect(screen.getByTestId("ui-tab-screens")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("screen-SCR-002")).toHaveClass("screen-card--focused");
  });

  it("shows the gallery: five states per screen with an explicit «не описано», elements, dev link vs honest text", async () => {
    const user = userEvent.setup();
    renderPhase();
    await screen.findByTestId("scenario-SCN-001");
    await user.click(screen.getByTestId("ui-tab-screens"));
    const gallery = screen.getByTestId("ui-gallery");
    expect(within(gallery).getAllByRole("article")).toHaveLength(2);

    const status = screen.getByTestId("screen-SCR-001");
    expect(status).toHaveTextContent("/status");
    expect(status).toHaveTextContent("Показывает результат последней проверки.");
    expect(screen.getByTestId("state-SCR-001-error")).toHaveTextContent("Probe недоступен");
    expect(screen.getByTestId("states-SCR-001")).not.toHaveTextContent("не описано");
    const link = screen.getByTestId("dev-link-SCR-001");
    expect(link).toHaveAttribute("href", "https://dev.example.test/status");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
    expect(screen.getByTestId("element-SCR-001-EL-refresh")).toHaveTextContent("Button");

    const history = screen.getByTestId("screen-SCR-002");
    expect(screen.getByTestId("state-SCR-002-empty")).toHaveTextContent("пусто: не описано");
    expect(screen.getByTestId("state-SCR-002-error")).toHaveTextContent("ошибка: не описано");
    // A state present with no description counts as not described too.
    expect(screen.getByTestId("state-SCR-002-access")).toHaveTextContent("доступ: не описано");
    expect(screen.getByTestId("state-SCR-002-success")).toHaveTextContent("Таблица последних 50 проверок");
    expect(history).toHaveTextContent("dev-окружение появится после доставки");
    expect(screen.queryByTestId("dev-link-SCR-002")).toBeNull();
    expect(screen.getByTestId("ui-components")).toHaveTextContent("Table (SCR-002)");
    expect(document.body.textContent).not.toMatch(/\d+\s?%/);
  });

  it("comments on an element through the parent's fragment flow and marks a detached anchor explicitly", async () => {
    const user = userEvent.setup();
    const { onSelectFragment } = renderPhase();
    await screen.findByTestId("scenario-SCN-001");
    await user.click(screen.getByTestId("ui-tab-screens"));
    await user.click(screen.getByTestId("comment-SCR-001-EL-refresh"));
    expect(onSelectFragment).toHaveBeenCalledWith(SCREEN_STATUS_PATH, "EL-refresh");
    expect(screen.getByTestId("element-SCR-001-EL-refresh")).toHaveTextContent("замечаний: 1");
    // The detached remark points at an element that no longer exists: shown, never re-attached.
    expect(screen.getByTestId("orphaned-SCR-001")).toHaveTextContent("привязка потеряна");
    expect(screen.getByTestId("orphaned-SCR-001")).toHaveTextContent("cmt_el_detached_004 (EL-old-spinner)");
  });

  it("marks a detached anchor on an existing element", async () => {
    const user = userEvent.setup();
    renderPhase(uiSpecView, { comments: [{ ...commentElementDetached, anchor: { ...commentElementDetached.anchor, anchor_id: "EL-status-badge" } }] });
    await screen.findByTestId("scenario-SCN-001");
    await user.click(screen.getByTestId("ui-tab-screens"));
    expect(screen.getByTestId("element-SCR-001-EL-status-badge")).toHaveTextContent("привязка потеряна");
    expect(screen.getByTestId("element-SCR-001-EL-refresh")).not.toHaveTextContent("привязка потеряна");
  });

  it("lists the links from → to with trigger and condition", async () => {
    const user = userEvent.setup();
    renderPhase();
    await screen.findByTestId("scenario-SCN-001");
    await user.click(screen.getByTestId("ui-tab-links"));
    const row = screen.getByTestId("link-SCR-001->SCR-002");
    expect(row).toHaveTextContent("Статус сервиса");
    expect(row).toHaveTextContent("История проверок");
    expect(row).toHaveTextContent("клик «История»");
    expect(row).toHaveTextContent("есть хотя бы одна проверка");
  });

  it("is honest: no branch, empty spec, parse errors and a 503", async () => {
    renderPhase(uiSpecView, { tree: artifactTreeNoBranch });
    expect(screen.getByTestId("interface-no-branch")).toHaveTextContent("Ветки изменения ещё нет");
  });

  it("says the spec is empty and shows parse errors as a warning", async () => {
    renderPhase({ ...uiSpecView, scenarios: [], screens: [], links: [], components: [], errors: ["design/ui/screens/SCR-009.md: states must be a mapping"] });
    await screen.findByTestId("interface-empty");
    expect(screen.getByText(/Не разобраны/)).toHaveTextContent("SCR-009.md");
  });

  it("shows the 503 detail of the UI read", async () => {
    renderPhase(null);
    await screen.findByText(/UI-спека недоступна/);
    expect(screen.getByText(/UI-спека недоступна/)).toHaveTextContent("the product repository is not configured in this contour");
  });
});
