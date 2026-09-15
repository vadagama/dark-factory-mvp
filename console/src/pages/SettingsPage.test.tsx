import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SettingsPage } from "./SettingsPage";
import { setToken } from "../api/token";

const TOKEN_KEY = "dark_factory_operator_token";

describe("SettingsPage", () => {
  it("shows the masked suffix of a stored token, never the value", () => {
    setToken("abcd1234efgh");
    render(<SettingsPage />);
    const masked = screen.getByTestId("token-masked");
    expect(masked).toHaveTextContent("••••••••efgh");
    // The full token must not leak into the DOM.
    expect(masked).not.toHaveTextContent("abcd1234");
    expect(screen.getByRole("button", { name: "Удалить токен" })).toBeEnabled();
  });

  it("prompts for a token when none is stored", () => {
    render(<SettingsPage />);
    expect(screen.getByTestId("token-absent")).toHaveTextContent("Токен не задан");
    expect(screen.getByRole("button", { name: "Удалить токен" })).toBeDisabled();
  });

  it("saves a token into localStorage via a masked input", async () => {
    const user = userEvent.setup();
    render(<SettingsPage />);
    const input = screen.getByLabelText("Новый токен");
    expect(input).toHaveAttribute("type", "password");
    await user.type(input, "synthetic-token-value");
    await user.click(screen.getByRole("button", { name: "Сохранить токен" }));
    expect(window.localStorage.getItem(TOKEN_KEY)).toBe("synthetic-token-value");
    // Input is cleared and the stored token is displayed masked.
    expect(input).toHaveValue("");
    expect(screen.getByTestId("token-masked")).toHaveTextContent("••••••••alue");
  });

  it("removes the stored token on demand", async () => {
    setToken("abcd1234efgh");
    const user = userEvent.setup();
    render(<SettingsPage />);
    await user.click(screen.getByRole("button", { name: "Удалить токен" }));
    expect(window.localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(screen.getByTestId("token-absent")).toHaveTextContent("Токен не задан");
  });

  it("shows the informational factory mode derived from the snapshot", () => {
    render(<SettingsPage />);
    const mode = screen.getByTestId("factory-mode");
    expect(mode).toHaveTextContent("С согласованиями");
    // Each <li> renders the label and the derived value; empty lists wrap
    // their placeholder in a <span>, non-empty lists join into the text node.
    expect(screen.getByText("Human-гейты: specification, review")).toBeInTheDocument();
    expect(screen.getByText("Auto-merge риск-классы:")).toBeInTheDocument();
    expect(screen.getByText("нет (merge — только человек, ADR-011)")).toBeInTheDocument();
  });

  it("lists the role profiles from the snapshot", () => {
    render(<SettingsPage />);
    const profiles = screen.getByTestId("profiles-list");
    expect(profiles).toHaveTextContent("Develop");
    expect(profiles).toHaveTextContent("Product");
    expect(profiles).toHaveTextContent("Quality");
  });

  it("persists an API base URL override", async () => {
    const user = userEvent.setup();
    render(<SettingsPage />);
    const input = screen.getByLabelText("Base URL");
    await user.clear(input);
    await user.type(input, "http://127.0.0.1:9000/api/v1");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(window.localStorage.getItem("dark_factory_api_base_url")).toBe(
      "http://127.0.0.1:9000/api/v1",
    );
  });
});
