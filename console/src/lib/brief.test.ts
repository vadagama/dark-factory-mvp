import { describe, expect, it } from "vitest";
import {
  briefAuthor,
  briefToFields,
  deriveBriefStatus,
  fieldsToBrief,
  parseCostBudget,
  parseTokenBudget,
  splitLines,
} from "./brief";
import { briefComplete } from "../test/fixtures";

describe("brief helpers", () => {
  it("splits textarea lines, trimming and dropping blanks in order", () => {
    expect(splitLines(" a \n\n b\r\nc ")).toEqual(["a", "b", "c"]);
  });

  it("derives the status like the server: complete iff problem and goal", () => {
    expect(deriveBriefStatus({ problem: "p", goal: "g", constraints: "", out_of_scope: "" })).toBe("complete");
    expect(deriveBriefStatus({ problem: "p", goal: " ", constraints: "", out_of_scope: "" })).toBe("draft");
  });

  it("round-trips a brief through form fields", () => {
    const fields = briefToFields(briefComplete);
    expect(fields.constraints).toBe("Без новых зависимостей");
    const brief = fieldsToBrief(fields, { source_text: briefComplete.source_text, formulated_by: "agent" });
    expect(brief).toEqual(briefComplete);
  });

  it("keeps the agent as author only while the wording is unchanged", () => {
    const agent = briefToFields(briefComplete);
    expect(briefAuthor(agent, agent)).toBe("agent");
    expect(briefAuthor({ ...agent, goal: "edited" }, agent)).toBe("operator");
    expect(briefAuthor(agent, null)).toBe("operator");
  });

  it("validates the spend limit like SpendLimit does", () => {
    expect(parseCostBudget("25")).toBe("25");
    expect(parseCostBudget("25,5")).toBe("25.5");
    expect(parseCostBudget("0")).toBeNull();
    expect(parseCostBudget("-1")).toBeNull();
    expect(parseCostBudget("1.12345")).toBeNull();
    expect(parseTokenBudget("")).toBeNull();
    expect(parseTokenBudget("150000")).toBe(150000);
    expect(parseTokenBudget("0")).toBeUndefined();
    expect(parseTokenBudget("abc")).toBeUndefined();
  });
});
