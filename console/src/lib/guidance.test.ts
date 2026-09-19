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
    expect(parseGuidanceApi("POST /changes/chg-1/approvals", change)).toEqual({ kind: "approve_phase", changeId: "chg-1" });
  });

  it("maps the M2 workspace actions (approve, rework, questions, artifacts)", () => {
    expect(parseGuidanceApi("POST /changes/chg-1/rework-orders", change)).toEqual({ kind: "rework", changeId: "chg-1" });
    expect(parseGuidanceApi("GET /changes/chg-1/questions?status=open", change)).toEqual({
      kind: "focus_questions",
      changeId: "chg-1",
    });
    expect(parseGuidanceApi("GET /changes/chg-1/questions", change)).toEqual({ kind: "focus_questions", changeId: "chg-1" });
    expect(parseGuidanceApi("GET /changes/chg-1/artifacts", change)).toEqual({ kind: "open_artifacts", changeId: "chg-1" });
  });

  it("maps the M3 intents: approve with the phase of the CLI hint, waive UI, request an alternative", () => {
    expect(
      parseGuidanceApi("POST /changes/chg-1/approvals", change, {
        cli: "factory change approve --id chg-1 --phase architecture",
        label: "Согласовать архитектуру",
      }),
    ).toEqual({ kind: "approve_phase", changeId: "chg-1", phase: "architecture" });
    expect(
      parseGuidanceApi("POST /changes/chg-1/approvals", change, {
        cli: "factory change approve --id chg-1 --phase interface",
        label: "Согласовать интерфейс",
      }),
    ).toEqual({ kind: "approve_phase", changeId: "chg-1", phase: "interface" });
    // Requirements keep the plain approval: the phase is not one the parser fixes.
    expect(
      parseGuidanceApi("POST /changes/chg-1/approvals", change, {
        cli: "factory change approve --id chg-1 --phase requirements",
        label: "Согласовать требования",
      }),
    ).toEqual({ kind: "approve_phase", changeId: "chg-1" });
    expect(
      parseGuidanceApi("POST /changes/chg-1/approvals", change, {
        cli: "factory change approve --id chg-1 --phase interface --waive --comment …",
        label: "Подтвердить пропуск UI",
      }),
    ).toEqual({ kind: "waive_phase", changeId: "chg-1", phase: "interface", gate: "ui" });
    expect(parseGuidanceApi("POST /changes/chg-1/decisions/adr:prd:0002/alternative", change)).toEqual({
      kind: "request_alternative",
      changeId: "chg-1",
      decisionId: "adr:prd:0002",
    });
    expect(
      parseGuidanceApi("POST /changes/chg-1/approvals", change, { cli: null, label: "Запросить альтернативу" }),
    ).toEqual({ kind: "request_alternative", changeId: "chg-1", decisionId: null });
    // Hints never turn an unrelated api into an approval.
    expect(parseGuidanceApi("GET /changes/chg-1/artifacts", change, { cli: "approve --phase architecture", label: "x" })).toEqual({
      kind: "open_artifacts",
      changeId: "chg-1",
    });
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
