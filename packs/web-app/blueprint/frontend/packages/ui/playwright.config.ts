import { defineConfig } from "@playwright/test";

// Visual regression gate of the Small UIKit (T-042, ADR-014): the built
// Storybook (`storybook-static`, served by scripts/static-server.mjs) is the
// single source of screenshots. Determinism contract (see packs/ui/rules.md):
// - pinned browser image in CI: mcr.microsoft.com/playwright, version locked
//   to @playwright/test in package-lock.json (digest pinned in the factory CI);
// - fixed viewport, device scale factor, locale, timezone, color scheme and
//   reduced motion;
// - animations disabled in screenshots;
// - maxDiffPixelRatio 0.01 — an intentional rendering jitter allowance;
// - baselines are committed and updated ONLY via review (regenerate them in
//   the pinned container: npm run test:visual -- --update-snapshots).
export default defineConfig({
  testDir: "./tests/visual",
  timeout: 60_000,
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" },
  },
  use: {
    baseURL: "http://127.0.0.1:6006",
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
    locale: "en-US",
    timezoneId: "UTC",
    colorScheme: "light",
    reducedMotion: "reduce",
  },
  webServer: {
    command: "node scripts/static-server.mjs",
    url: "http://127.0.0.1:6006/index.json",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
  // Baselines live next to the spec and carry no platform suffix: the browser
  // is pinned (same container locally and in CI), so OS-dependent names would
  // only make committed baselines unusable across machines.
  snapshotPathTemplate: "{snapshotDir}/{testFileDir}/{testFileName}-snapshots/{arg}{ext}",
});
