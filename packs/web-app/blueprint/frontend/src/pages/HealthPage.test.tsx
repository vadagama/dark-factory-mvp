import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HealthPage } from "./HealthPage";
import * as client from "../api/client";

describe("HealthPage", () => {
  it("shows backend and database status when healthy", async () => {
    vi.spyOn(client, "fetchHealth").mockResolvedValue({ status: "ok", database: "ok" });

    render(<HealthPage />);

    const markers = await screen.findAllByText("ok", { selector: "strong" });
    expect(markers).toHaveLength(2);
  });

  it("shows an alert with a retry action when the backend fails", async () => {
    vi.spyOn(client, "fetchHealth").mockRejectedValue(new client.ApiError(0, "The API is unreachable"));

    render(<HealthPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Backend unavailable");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
