import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { CiStagesPage, describeToggleError, enabledLabel, groupStages } from "./CiStagesPage";
import { ApiError } from "../api/client";
import * as token from "../api/token";
import { ciStage, ciStageOff, ciStages, ciStagesUnavailable } from "../test/fixtures";
import { bodyOf, jsonResponse, stubFetch } from "../test/mock-fetch";
import type { CiStage, CiStages } from "../api/types";

const READ = { method: "GET", pattern: /\/api\/v1\/ci\/stages$/ };

function problem(status: number, detail: string): object {
  return { type: "about:blank", title: "Error", status, detail };
}

function renderPage(): void {
  render(
    <MemoryRouter initialEntries={["/ci"]}>
      <CiStagesPage />
    </MemoryRouter>,
  );
}

describe("enabledLabel", () => {
  it("renders the three wire states as text, not colour", () => {
    expect(enabledLabel(true)).toBe("включён");
    expect(enabledLabel(false)).toBe("выключен");
    expect(enabledLabel(null)).toBe("неизвестно");
  });
});

describe("groupStages", () => {
  it("keeps the server order inside a group and drops empty groups", () => {
    const stages: CiStage[] = [
      { ...ciStage, job: "test", group: "python" },
      { ...ciStage, job: "lint", group: "python" },
      { ...ciStage, job: "console-image", group: "image" },
    ];
    const buckets = groupStages(stages);
    expect(buckets.map((bucket) => bucket.group)).toEqual(["python", "image"]);
    expect(buckets[0].title).toBe("Python-гейты");
    expect(buckets[0].stages.map((stage) => stage.job)).toEqual(["test", "lint"]);
    expect(buckets[1].title).toBe("Доверенные сборки образов");
  });
});

describe("describeToggleError", () => {
  it("names the scope and the screen to fix 401 / 403", () => {
    expect(describeToggleError(new ApiError(401, null, "no token"), "lint")).toContain("ci:write");
    expect(describeToggleError(new ApiError(403, null, "no scope"), "lint")).toContain("operator");
    expect(describeToggleError(new ApiError(404, null, "?"), "lint")).toContain("lint");
    expect(describeToggleError(new ApiError(503, null, "?"), "lint")).toContain(
      "не сконфигурированы на этом контуре",
    );
    expect(describeToggleError(new ApiError(502, null, "boom"), "lint")).toContain("GitHub");
    // Unknown statuses fall back to the server detail — never invented.
    expect(describeToggleError(new ApiError(500, null, "kaboom"), "lint")).toBe("kaboom");
  });
});

describe("CiStagesPage", () => {
  it("renders the stages grouped, with their switches and a token-free read", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    const fetchMock = stubFetch([{ ...READ, handler: () => jsonResponse(200, ciStages) }]);
    renderPage();

    const lintRow = await screen.findByTestId("ci-stage-lint");
    expect(lintRow).toHaveTextContent("Ruff lint + format");
    expect(lintRow).toHaveTextContent("CI_SKIP_LINT");
    expect(lintRow).toHaveTextContent("Стиль, импорты и форматирование");
    expect(lintRow).toHaveTextContent("uv run ruff check .");
    expect(lintRow).toHaveTextContent("лёгкий");

    expect(screen.getByText("Python-гейты")).toBeInTheDocument();
    expect(screen.getByText("Console-гейты")).toBeInTheDocument();
    expect(screen.getByTestId("ci-repository")).toHaveTextContent("vadagama/dark-factory-mvp");
    expect(screen.getByTestId("ci-safety-banner")).toHaveTextContent("Перед merge верните все этапы");

    expect(screen.getByRole("switch", { name: "Переключатель этапа lint" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("switch", { name: "Переключатель этапа console-e2e" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("включён");
    expect(screen.getByTestId("ci-stage-state-console-e2e")).toHaveTextContent("выключен");
    expect(screen.getByTestId("ci-off-count")).toHaveTextContent("Выключено этапов: 1");

    // Reads stay token-free even when a token is stored (ADR-021 p.4).
    const read = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(read[1].method).toBe("GET");
    expect((read[1].headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("toggles one stage with a token-bearing PUT and applies the returned stage", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    const fetchMock = stubFetch([
      { ...READ, handler: () => jsonResponse(200, ciStages) },
      {
        method: "PUT",
        pattern: /\/api\/v1\/ci\/stages\/lint$/,
        handler: () => jsonResponse(200, { ...ciStage, enabled: false }),
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    const lintSwitch = await screen.findByRole("switch", { name: "Переключатель этапа lint" });
    await user.click(lintSwitch);

    await waitFor(() =>
      expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("выключен"),
    );
    expect(lintSwitch).toHaveAttribute("aria-checked", "false");
    expect(screen.getByTestId("ci-off-count")).toHaveTextContent("Выключено этапов: 2");

    const put = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit).method === "PUT",
    ) as unknown as [string, RequestInit];
    expect(put[0]).toBe("http://localhost:3000/api/v1/ci/stages/lint");
    const headers = put[1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer operator-token");
    expect(headers["Idempotency-Key"]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    expect(bodyOf(put[1])).toEqual({ enabled: false });
  });

  it("disables every switch and shows the server reason when the API is not configured", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    stubFetch([{ ...READ, handler: () => jsonResponse(200, ciStagesUnavailable) }]);
    renderPage();

    const banner = await screen.findByTestId("ci-unavailable-banner");
    expect(banner).toHaveTextContent("GitHub credentials are not configured on this contour");
    const lintSwitch = screen.getByRole("switch", { name: "Переключатель этапа lint" });
    expect(lintSwitch).toBeDisabled();
    expect(lintSwitch).toHaveAttribute("aria-checked", "mixed");
    expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("неизвестно");
    expect(screen.getByRole("button", { name: "Включить все этапы" })).toBeDisabled();
    expect(screen.getByTestId("ci-repository")).toHaveTextContent("недоступен");
  });

  it("asks for a ci:write token on 401 and never pretends the write happened", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    stubFetch([
      { ...READ, handler: () => jsonResponse(200, ciStages) },
      {
        method: "PUT",
        pattern: /\/api\/v1\/ci\/stages\/lint$/,
        handler: () =>
          jsonResponse(401, problem(401, "A valid bearer token is required")),
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    const lintSwitch = await screen.findByRole("switch", { name: "Переключатель этапа lint" });
    await user.click(lintSwitch);

    expect(await screen.findByText(/введите его на экране/)).toBeInTheDocument();
    // TokenDialog (Radix) opens for the fail-closed flow.
    expect(await screen.findByText("Нужен токен оператора")).toBeInTheDocument();
    expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("включён");
    expect(lintSwitch).toHaveAttribute("aria-checked", "true");
  });

  it("reports 503 as 'not configured on this contour'", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    stubFetch([
      { ...READ, handler: () => jsonResponse(200, ciStages) },
      {
        method: "PUT",
        pattern: /\/api\/v1\/ci\/stages\/lint$/,
        handler: () => jsonResponse(503, problem(503, "CI toggles are not configured")),
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("switch", { name: "Переключатель этапа lint" }));

    expect(
      await screen.findByText(/не сконфигурированы на этом контуре/),
    ).toBeInTheDocument();
    expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("включён");
  });

  it("re-enables every off stage sequentially and reports the failures", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    const off: CiStages = {
      ...ciStages,
      stages: [
        { ...ciStage, enabled: false },
        { ...ciStageOff, enabled: false },
      ],
    };
    // After the reload the server reports lint on (its PUT succeeded) and
    // console-e2e still off (its PUT failed) — the UI must say so.
    const settled: CiStages = {
      ...ciStages,
      stages: [{ ...ciStage, enabled: true }, { ...ciStageOff, enabled: false }],
    };
    let reads = 0;
    const puts: { job: string; body: Record<string, unknown> }[] = [];
    stubFetch([
      {
        ...READ,
        handler: () => {
          reads += 1;
          return jsonResponse(200, reads === 1 ? off : settled);
        },
      },
      {
        method: "PUT",
        pattern: /\/api\/v1\/ci\/stages\/(lint|console-e2e)$/,
        handler: (url, init) => {
          const job = url.pathname.split("/").pop() ?? "";
          puts.push({ job, body: bodyOf(init) });
          return job === "console-e2e"
            ? jsonResponse(502, problem(502, "GitHub call failed"))
            : jsonResponse(200, { ...ciStage, job, enabled: true });
        },
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    const bulk = await screen.findByRole("button", { name: "Включить все этапы" });
    expect(bulk).toBeEnabled();
    expect(screen.getByTestId("ci-off-count")).toHaveTextContent("Выключено этапов: 2");
    await user.click(bulk);

    expect(await screen.findByText(/Не удалось включить 1 из 2/)).toBeInTheDocument();
    expect(puts.map((entry) => entry.job)).toEqual(["lint", "console-e2e"]);
    expect(puts[0].body).toEqual({ enabled: true });

    // The reload settles on the server state and drops the local patches.
    await waitFor(() =>
      expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("включён"),
    );
    expect(screen.getByTestId("ci-stage-state-console-e2e")).toHaveTextContent("выключен");
    expect(screen.getByTestId("ci-off-count")).toHaveTextContent("Выключено этапов: 1");
  });

  it("disables the bulk action when nothing is switched off", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    stubFetch([
      {
        ...READ,
        handler: () => jsonResponse(200, { ...ciStages, stages: [{ ...ciStage, enabled: true }] }),
      },
    ]);
    renderPage();

    expect(await screen.findByRole("button", { name: "Включить все этапы" })).toBeDisabled();
  });

  it("disables the switch while the write is in flight", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    // Gated PUT: the switch must be frozen until the server answers.
    const deferred: { resolve: () => void } = { resolve: () => {} };
    const gate = new Promise<void>((resolve) => {
      deferred.resolve = resolve;
    });
    stubFetch([
      { ...READ, handler: () => jsonResponse(200, ciStages) },
      {
        method: "PUT",
        pattern: /\/api\/v1\/ci\/stages\/lint$/,
        handler: async () => {
          await gate;
          return jsonResponse(200, { ...ciStage, enabled: false });
        },
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    const lintSwitch = await screen.findByRole("switch", { name: "Переключатель этапа lint" });
    await user.click(lintSwitch);
    await waitFor(() => expect(lintSwitch).toBeDisabled());
    expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("включён");

    deferred.resolve();
    await waitFor(() => expect(lintSwitch).toBeEnabled());
    expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("выключен");
  });

  it("disables the bulk action while it runs", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("operator-token");
    const off: CiStages = { ...ciStages, stages: [{ ...ciStage, enabled: false }] };
    const on: CiStages = { ...ciStages, stages: [{ ...ciStage, enabled: true }] };
    const deferred: { resolve: () => void } = { resolve: () => {} };
    const gate = new Promise<void>((resolve) => {
      deferred.resolve = resolve;
    });
    let reads = 0;
    stubFetch([
      {
        ...READ,
        handler: () => {
          reads += 1;
          return jsonResponse(200, reads === 1 ? off : on);
        },
      },
      {
        method: "PUT",
        pattern: /\/api\/v1\/ci\/stages\/lint$/,
        handler: async () => {
          await gate;
          return jsonResponse(200, { ...ciStage, enabled: true });
        },
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Включить все этапы" }));
    expect(await screen.findByRole("button", { name: "Включение…" })).toBeDisabled();

    deferred.resolve();
    expect(await screen.findByText(/Включены все этапы \(1\)/)).toBeInTheDocument();
    // The success toast renders before the post-mutation refetch lands; wait
    // for the refreshed stage state instead of asserting synchronously.
    await waitFor(() =>
      expect(screen.getByTestId("ci-stage-state-lint")).toHaveTextContent("включён"),
    );
  });

  it("shows an error state with a retry button when the list fails", async () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    let reads = 0;
    stubFetch([
      {
        ...READ,
        handler: () => {
          reads += 1;
          return reads === 1
            ? jsonResponse(500, problem(500, "database is down"))
            : jsonResponse(200, ciStages);
        },
      },
    ]);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/database is down/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByTestId("ci-stage-lint")).toBeInTheDocument();
  });
});
