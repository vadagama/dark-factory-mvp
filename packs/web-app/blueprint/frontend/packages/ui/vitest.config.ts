import { defineConfig } from "vitest/config";

// Vitest of the Small UIKit (T-042): component/pattern behavior, the axe a11y
// suite and the DTCG tokens drift test — all in jsdom, no network, seconds.
export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
