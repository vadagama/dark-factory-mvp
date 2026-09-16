import { expect, test } from "@playwright/test";

/**
 * Visual regression gate of the Small UIKit (T-042, ADR-014).
 *
 * The suite screenshots EVERY story of the built Storybook (the executable UI
 * spec), so a new component or state is automatically covered after its first
 * baseline is regenerated. Determinism is owned by playwright.config.ts:
 * pinned browser image in CI, fixed viewport/locale/timezone/color scheme/
 * reduced motion, disabled animations, maxDiffPixelRatio 0.01.
 *
 * Baselines are committed and updated ONLY via review — regenerate them in
 * the pinned container (`npm run test:visual -- --update-snapshots`), inspect
 * the diff in the MR, and let the reviewer approve the visual change.
 */

const STORYBOOK = "http://127.0.0.1:6006";

interface StoryEntry {
  id: string;
  type: string;
}

interface StoryIndex {
  entries: Record<string, StoryEntry>;
}

test.beforeAll(async () => {
  const response = await fetch(`${STORYBOOK}/index.json`);
  expect(response.ok, "index.json of the built storybook must be reachable").toBe(true);
  const index = (await response.json()) as StoryIndex;
  const stories = Object.values(index.entries).filter((entry) => entry.type === "story");
  expect(stories.length, "the spec must contain stories to screenshot").toBeGreaterThan(0);
  storyIds.push(...stories.map((story) => story.id));
});

// Filled in beforeAll; sorted for a stable execution order across runs.
const storyIds: string[] = [];

test("every story matches its committed visual baseline", async ({ page }) => {
  // One iteration over ALL stories takes minutes (goto + settle + screenshot
  // each), which exceeds the per-test default of playwright.config.ts; the
  // suite-level timeout owns the budget instead.
  test.setTimeout(300_000);
  expect(storyIds.length).toBeGreaterThan(0);
  const failures: string[] = [];
  for (const id of [...storyIds].sort()) {
    try {
      await page.goto(`${STORYBOOK}/iframe.html?id=${id}&viewMode=story`);
      // Portal-aware: stories rendering through a Radix portal (open Dialog/
      // ConfirmDialog, open Select popper) leave #storybook-root EMPTY — the
      // content is attached as a direct child of <body> (verified in the built
      // Storybook DOM: div[role=dialog].small-dialog__content with the overlay
      // sibling; popper wrapper div for Select). A root-only wait would time
      // out on exactly those stories.
      await page.waitForSelector(
        "#storybook-root > *, #storybook-docs > *, [role=\"dialog\"], [data-radix-popper-content-wrapper]",
        {
          state: "attached",
          timeout: 15_000,
        },
      );
      // Let Radix portals and layout settle; the frame itself is static.
      await page.waitForTimeout(250);
      await expect(page).toHaveScreenshot(`${id}.png`, {
        animations: "disabled",
        maxDiffPixelRatio: 0.01,
      });
    } catch (error) {
      failures.push(`${id}: ${error instanceof Error ? error.message.split("\n")[0] : error}`);
    }
  }
  expect(
    failures,
    `${failures.length} of ${storyIds.length} stories diverged from their baselines`,
  ).toEqual([]);
});
