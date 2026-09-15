import { describe, expect, it } from "vitest";
import { newChangeId, uuidV4 } from "./id";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("uuidV4", () => {
  it("produces RFC 4122 v4 ids", () => {
    const value = uuidV4();
    expect(value).toMatch(UUID_V4);
    expect(uuidV4()).not.toBe(value);
  });
});

describe("newChangeId", () => {
  it("follows the chg_<12 hex> convention", () => {
    expect(newChangeId()).toMatch(/^chg_[0-9a-f]{12}$/);
  });
});
