import { describe, expect, it } from "vitest";
import { deriveExpectedStateRevision, factoryModeLabel, meta } from "./meta";

describe("deriveExpectedStateRevision", () => {
  it("starts at 1 for a change with no decisions", () => {
    expect(deriveExpectedStateRevision(0)).toBe(1);
  });

  it("adds one per recorded decision (T035 semantics)", () => {
    expect(deriveExpectedStateRevision(2)).toBe(3);
  });
});

describe("factoryModeLabel", () => {
  it("labels every supported mode", () => {
    expect(factoryModeLabel("with_approvals")).toBe("С согласованиями");
    expect(factoryModeLabel("autonomous_until_mr")).toBe("Автономно до MR");
    expect(factoryModeLabel("manual")).toBe("Ручной режим");
  });
});

describe("meta snapshot", () => {
  it("reflects the committed defaults (with_approvals, squash-only)", () => {
    expect(meta.schema_version).toBe(1);
    expect(meta.factory_mode.mode).toBe("with_approvals");
    expect(meta.factory_mode.human_gates).toEqual(["specification", "review"]);
    expect(meta.factory_mode.auto_merge_risk_classes).toEqual([]);
    expect(meta.limits.max_rework_rounds).toBe(3);
    expect(meta.profiles.map((profile) => profile.role)).toEqual([
      "architect",
      "design",
      "develop",
      "product",
      "quality",
    ]);
  });
});
