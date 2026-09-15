import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { IntakeForm } from "./IntakeForm";
import * as token from "../api/token";
import { jsonResponse, stubFetch, bodyOf } from "../test/mock-fetch";

function renderForm(): void {
  render(<IntakeForm onTokenRequired={() => undefined} />);
}

describe("IntakeForm", () => {
  it("is disabled without a token (fail-closed)", () => {
    vi.spyOn(token, "getToken").mockReturnValue(null);
    renderForm();
    expect(screen.getByTestId("intake-token-hint")).toHaveTextContent("Мутации недоступны");
    expect(screen.getByRole("button", { name: "Создать изменение" })).toBeDisabled();
  });

  it("submits a console-sourced change with Bearer auth and idempotency key", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("synthetic-token");
    const fetchMock = stubFetch([
      {
        method: "POST",
        pattern: /\/api\/v1\/changes$/,
        handler: (_url, init) => jsonResponse(201, bodyOf(init)),
      },
    ]);
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText("Название"), "Wire rate limiting");
    await user.type(screen.getByLabelText("Репозиторий (slug)"), "acme/demo-service");
    await user.selectOptions(screen.getByLabelText("Класс риска"), "R2");
    await user.click(screen.getByRole("button", { name: "Создать изменение" }));

    await screen.findByText(/Изменение создано: chg_/);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/changes");
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer synthetic-token");
    expect(headers["Idempotency-Key"]).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    const body = bodyOf(init);
    expect(body.source).toBe("console");
    expect(body.title).toBe("Wire rate limiting");
    expect(body.risk_class).toBe("R2");
    expect(body.product).toEqual({ provider: "github", slug: "acme/demo-service" });
    expect(String(body.id)).toMatch(/^chg_[0-9a-f]{12}$/);
  });

  it("surfaces a 401 as an error notice instead of failing silently", async () => {
    vi.spyOn(token, "getToken").mockReturnValue("stale-token");
    stubFetch([
      {
        method: "POST",
        pattern: /\/api\/v1\/changes$/,
        handler: () =>
          jsonResponse(401, {
            type: "about:blank",
            title: "Unauthorized",
            status: 401,
            detail: "A valid bearer token is required",
          }),
      },
    ]);
    const user = userEvent.setup();
    renderForm();
    await user.type(screen.getByLabelText("Название"), "Demo");
    await user.type(screen.getByLabelText("Репозиторий (slug)"), "acme/demo-service");
    await user.click(screen.getByRole("button", { name: "Создать изменение" }));
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("Ошибка приёмки");
    expect(notice).toHaveTextContent("A valid bearer token is required");
  });
});
