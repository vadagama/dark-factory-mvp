import { describe, expect, it } from "vitest";
import { actorLabel, parseGuidanceApi } from "./guidance";

const product = { kind: "product", id: "prd_1" } as const;
const change = { kind: "change", id: "chg_1" } as const;

describe("parseGuidanceApi", () => {
  it("maps the server api strings the Console can perform", () => {
    expect(parseGuidanceApi("POST /products/prd-1/validate", product)).toEqual({
      kind: "validate_product",
      productId: "prd-1",
    });
    expect(parseGuidanceApi("GET /products/prd-1", product)).toEqual({ kind: "reload_product", productId: "prd-1" });
    expect(parseGuidanceApi("POST /changes", product)).toEqual({ kind: "new_change", productId: "prd_1" });
    expect(parseGuidanceApi("POST /changes", change)).toEqual({ kind: "new_change", productId: null });
    expect(parseGuidanceApi("PUT /changes/chg-1/brief", change)).toEqual({ kind: "edit_brief", changeId: "chg-1" });
    expect(parseGuidanceApi("GET /changes/chg-1", change)).toEqual({ kind: "open_change", changeId: "chg-1" });
    expect(parseGuidanceApi("GET /runs/run-1", change)).toEqual({ kind: "open_run", runId: "run-1" });
    expect(parseGuidanceApi("POST /changes/chg-1/approvals", change)).toEqual({ kind: "open_gates", changeId: "chg-1" });
  });

  it("returns null for actions the Console does not perform yet (CLI is the way)", () => {
    expect(parseGuidanceApi("POST /runs/run-1/withdraw", change)).toBeNull();
    expect(parseGuidanceApi(null, change)).toBeNull();
    expect(parseGuidanceApi("DELETE /products/prd-1", product)).toBeNull();
  });

  it("labels blocker actors in Russian", () => {
    expect(actorLabel("operator")).toBe("оператор");
    expect(actorLabel("agent")).toBe("агент");
    expect(actorLabel("ci")).toBe("CI");
    expect(actorLabel("factory")).toBe("фабрика");
    expect(actorLabel("external")).toBe("внешняя система");
  });
});
