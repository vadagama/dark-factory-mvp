import { describe, expect, it } from "vitest";
import {
  anchorForSelection,
  artifactName,
  budgetFact,
  coercePropertyValue,
  encodeArtifactPath,
  phaseIndexLabel,
  phaseState,
  shortRevision,
  splitFrontmatter,
} from "./artifacts";
import { SPEC_CONTENT, SPEC_PATH, phaseGateClosed, phaseGateOpen } from "../test/fixtures";

describe("encodeArtifactPath", () => {
  it("encodes every segment and keeps the slashes (server route is {path:path})", () => {
    expect(encodeArtifactPath(".factory/changes/2026/CHG-0001-percent/spec/requirements/REQ-001 percent.md")).toBe(
      ".factory/changes/2026/CHG-0001-percent/spec/requirements/REQ-001%20percent.md",
    );
    expect(encodeArtifactPath("/a/b/")).toBe("a/b");
    expect(encodeArtifactPath("spec/файл#1.md")).toBe("spec/%D1%84%D0%B0%D0%B9%D0%BB%231.md");
  });

  it("names the file and shortens a revision", () => {
    expect(artifactName(SPEC_PATH)).toBe("REQ-001-health.md");
    expect(shortRevision("a1b2c3d4e5f6a7b8c9")).toBe("a1b2c3d4e5f6");
    expect(shortRevision(null)).toBe("—");
    expect(phaseIndexLabel("requirements")).toBe("Ф1");
    expect(phaseIndexLabel("done")).toBe("—");
  });
});

describe("splitFrontmatter", () => {
  it("separates the raw block from the body without touching either", () => {
    const { frontmatter, body } = splitFrontmatter(SPEC_CONTENT);
    expect(frontmatter).toContain("id: REQ-001");
    expect(body.startsWith("# REQ-001 Health endpoint {#req-001}")).toBe(true);
    // Round-trip: the split is lossless.
    expect(`---\n${frontmatter}\n---\n${body}`).toBe(SPEC_CONTENT);
  });

  it("treats a document without or with an unterminated block as body only", () => {
    expect(splitFrontmatter("# Title\n")).toEqual({ frontmatter: null, body: "# Title\n" });
    expect(splitFrontmatter("---\nid: x\nno end")).toEqual({ frontmatter: null, body: "---\nid: x\nno end" });
  });
});

describe("anchorForSelection", () => {
  const anchors = ["REQ-001", "req-001", "цели-и-границы", "AC-1", "AC-2"];

  it("prefers a stable id found inside the selection", () => {
    expect(anchorForSelection("- AC-2: GET /health answers 503", anchors)).toBe("AC-2");
    expect(anchorForSelection("see REQ-001 and AC-1", anchors)).toBe("REQ-001");
  });

  it("matches a heading slug against the selected heading text", () => {
    expect(anchorForSelection("Цели и границы", anchors)).toBe("цели-и-границы");
  });

  it("falls back to the whole document when nothing in the selection is an anchor", () => {
    expect(anchorForSelection("just some prose", anchors)).toBeNull();
    expect(anchorForSelection("   ", anchors)).toBeNull();
  });
});

describe("coercePropertyValue", () => {
  it("keeps the type of the original scalar", () => {
    expect(coercePropertyValue(2, "3")).toBe(3);
    expect(coercePropertyValue(2, "x")).toBe("x");
    expect(coercePropertyValue(true, "false")).toBe(false);
    expect(coercePropertyValue("draft", "review")).toBe("review");
  });
});

describe("phaseState", () => {
  it("projects server facts only: approved, decision, active, rework, passed, pending", () => {
    expect(phaseState("requirements", "requirements", { ...phaseGateOpen, approved: true })).toBe("approved");
    expect(phaseState("requirements", "requirements", phaseGateOpen)).toBe("decision");
    expect(phaseState("requirements", "requirements", phaseGateClosed)).toBe("active");
    expect(phaseState("requirements", "requirements", { ...phaseGateClosed, rework_pending: true })).toBe("rework");
    expect(phaseState("initiative", "requirements", null)).toBe("passed");
    expect(phaseState("plan", "requirements", null)).toBe("pending");
    expect(phaseState("delivery", "done", null)).toBe("passed");
    expect(phaseState("initiative", null, null)).toBe("pending");
  });
});

describe("budgetFact", () => {
  it("sums recorded costs and is honest about missing ones", () => {
    expect(budgetFact(["0.2847", null, "0.1"])).toBe("0.3847");
    expect(budgetFact([null])).toBeNull();
    expect(budgetFact([])).toBeNull();
  });
});
