import { expect } from "vitest";
import axe from "axe-core";
import type { ReactElement } from "react";
import { render } from "@testing-library/react";

/**
 * A11y helper of the Small UIKit (T-042, ADR-014): renders `ui` and asserts
 * ZERO axe violations limited to the WCAG 2.0/2.1 A and AA rule tags. jsdom
 * covers only the DOM-derived part of WCAG (what axe can compute); the rest
 * is the human acceptance checklist (docs/wcag22-aa-checklist.md).
 */
export async function expectNoAxeViolations(ui: ReactElement, name: string): Promise<void> {
  const { container } = render(ui);
  const results = await axe.run(container, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
  });
  const summary = results.violations
    .map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).join(", ")}`)
    .join("; ");
  expect(summary, name).toBe("");
}
