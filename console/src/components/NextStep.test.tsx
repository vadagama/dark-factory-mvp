import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NextStep } from "./NextStep";
import { changeGuidanceDraft, changeGuidanceStart, productGuidanceCreated, productGuidanceReady } from "../test/fixtures";
import type { Guidance } from "../api/types";

describe("NextStep", () => {
  it("renders headline, why, blockers with the actor in Russian and the after line", () => {
    render(<NextStep guidance={productGuidanceCreated} onPerform={() => undefined} />);
    const block = screen.getByTestId("next-step");
    expect(block).toHaveTextContent("Продукт зарегистрирован, репозиторий ещё не проверен");
    expect(block).toHaveTextContent("Фабрика не подтверждала доступ к репозиторию");
    expect(screen.getByTestId("next-step-blockers")).toHaveTextContent(
      "Репозиторий не проверен — снимает: оператор — как: Запустите проверку",
    );
    expect(screen.getByTestId("next-step-after")).toHaveTextContent("После проверки: продукт станет ready");
    expect(screen.getByTestId("next-step-secondary")).toHaveTextContent("Показать продукт");
    expect(screen.getByTestId("next-step-secondary")).toHaveTextContent("factory product show --id prd_demo_002");
    // No percentage progress anywhere (ADR-033: no invented progress).
    expect(block.textContent).not.toMatch(/\d+\s?%/);
  });

  it("performs a known api action through the single primary button", async () => {
    const onPerform = vi.fn();
    const user = userEvent.setup();
    render(<NextStep guidance={productGuidanceCreated} onPerform={onPerform} />);
    const primary = screen.getByTestId("next-step-primary");
    expect(primary).toHaveTextContent("Проверить репозиторий");
    expect(primary).toBeEnabled();
    await user.click(primary);
    expect(onPerform).toHaveBeenCalledWith({ kind: "validate_product", productId: "prd_demo_002" });
    // Exactly one primary.
    expect(screen.getAllByTestId("next-step-primary")).toHaveLength(1);
  });

  it("maps POST /changes on a product subject to the intake of that product", async () => {
    const onPerform = vi.fn();
    const user = userEvent.setup();
    render(<NextStep guidance={productGuidanceReady} onPerform={onPerform} />);
    await user.click(screen.getByRole("button", { name: "Новая фича" }));
    expect(onPerform).toHaveBeenCalledWith({ kind: "new_change", productId: "prd_demo_001" });
  });

  it("falls back to the CLI command when the Console cannot perform the primary", () => {
    render(<NextStep guidance={changeGuidanceStart} onPerform={() => undefined} />);
    expect(screen.queryByTestId("next-step-primary")).not.toBeInTheDocument();
    const cli = screen.getByTestId("next-step-cli");
    expect(cli).toHaveTextContent("Запустить фазу «Требования»");
    expect(cli).toHaveTextContent("в CLI:");
    expect(cli.querySelector("code")).toHaveTextContent("factory run advance --change-id chg_demo_001");
    expect(screen.getByTestId("next-step-phase")).toHaveTextContent("фаза: initiative");
  });

  it("renders a disabled primary with the server reason (no dead ends)", () => {
    const guidance: Guidance = {
      ...changeGuidanceStart,
      primary: {
        label: "Смержить CR",
        cli: null,
        api: null,
        enabled: false,
        reason: "Помощник merge появится в M5 (T112–T113); пока — merge в провайдере.",
      },
    };
    render(<NextStep guidance={guidance} onPerform={() => undefined} />);
    const primary = screen.getByTestId("next-step-primary");
    expect(primary).toBeDisabled();
    expect(primary).toHaveTextContent("Смержить CR");
    expect(screen.getByTestId("next-step-reason")).toHaveTextContent("Помощник merge появится в M5");
  });

  it("shows disabled secondary actions with their reason", () => {
    render(<NextStep guidance={changeGuidanceDraft} onPerform={() => undefined} />);
    expect(screen.getByTestId("next-step-secondary")).toHaveTextContent(
      "Бриф не завершён: требования нельзя начать без problem и goal.",
    );
  });

  it("disables the primary while busy", () => {
    render(<NextStep guidance={productGuidanceCreated} onPerform={() => undefined} busy />);
    expect(screen.getByTestId("next-step-primary")).toBeDisabled();
  });
});
