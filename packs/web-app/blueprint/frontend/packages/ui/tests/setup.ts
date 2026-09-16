import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

// @testing-library/react auto-cleanup requires a global afterEach; vitest runs
// with explicit imports here, so the cleanup is wired manually.
afterEach(() => {
  cleanup();
});
