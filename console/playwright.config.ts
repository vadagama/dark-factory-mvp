import { defineConfig } from "@playwright/test";

// Playwright smoke (T036 DoD, ADR-021 p.6): 5 scenarios against the production
// build served by `vite preview`, with every api/v1 call intercepted on
// synthetic fixtures (e2e/fixtures/api.ts) — hermetic, no API/PostgreSQL needed.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    // vite preview binds to ::1 on macOS, so "localhost" (not 127.0.0.1) is used.
    baseURL: "http://localhost:4173",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run preview",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
