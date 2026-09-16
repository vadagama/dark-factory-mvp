import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { fileURLToPath } from "node:url";
import { generate, loadTokenFiles } from "../scripts/build-tokens.mjs";
import { tokens } from "../src/tokens";

/**
 * Tokens gate (T-042, ADR-014): the DTCG files are the source of truth, the
 * generated src/tokens.css and src/tokens.ts are committed, and this suite
 * fails on drift — regenerating in memory must reproduce the committed files
 * byte-for-byte. It also asserts the WCAG contrast of the color pairs the kit
 * actually renders (buttons, statuses, alerts).
 */
// Plain `new URL(literal, import.meta.url)` is intercepted by Vite's asset
// transform under vitest, so resolve from the module path instead.
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

describe("DTCG tokens", () => {
  it("generated files match an in-memory regeneration (drift gate)", () => {
    const files = loadTokenFiles();
    const { css, ts } = generate(files);
    expect(css).toBe(readFileSync(`${ROOT}/src/tokens.css`, "utf8"));
    expect(ts).toBe(readFileSync(`${ROOT}/src/tokens.ts`, "utf8"));
  });

  it("exposes every CSS custom property in tokens.css", () => {
    const css = readFileSync(`${ROOT}/src/tokens.css`, "utf8");
    const varNames = collectVars(tokens as unknown as Record<string, unknown>, "small");
    expect(varNames.length).toBeGreaterThan(30);
    for (const name of varNames) {
      expect(css).toContain(`--${name}:`);
    }
  });
});

function collectVars(node: Record<string, unknown>, prefix: string): string[] {
  const out: string[] = [];
  for (const [key, value] of Object.entries(node)) {
    // Same camelCase -> kebab-case mapping the generator applies per segment.
    const name = `${prefix}-${key.replace(/([a-z0-9])([A-Z])/g, "$1-$2").toLowerCase()}`;
    if (typeof value === "string" || typeof value === "number") {
      out.push(name);
    } else if (value !== null && typeof value === "object") {
      out.push(...collectVars(value as Record<string, unknown>, name));
    }
  }
  return out;
}

/** Relative luminance of a hex color per WCAG 2.x. */
function luminance(hex: string): number {
  const match = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!match) throw new Error(`not a 6-digit hex color: ${hex}`);
  const channels = [0, 2, 4].map((offset) => {
    const raw = Number.parseInt(match[1].slice(offset, offset + 2), 16) / 255;
    return raw <= 0.03928 ? raw / 12.92 : ((raw + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function contrast(foreground: string, background: string): number {
  const l1 = luminance(foreground);
  const l2 = luminance(background);
  const [lighter, darker] = l1 >= l2 ? [l1, l2] : [l2, l1];
  return (lighter + 0.05) / (darker + 0.05);
}

const color = tokens.color as Record<keyof typeof tokens.color, string>;

describe("WCAG contrast of rendered color pairs (AA: 4.5:1 for normal text)", () => {
  const colorTokens = color as unknown as Record<string, string>;

  it.each([
    ["text on bg", colorTokens["text"], colorTokens["bg"]],
    ["text on surface", colorTokens["text"], colorTokens["surface"]],
    ["muted text on bg", colorTokens["text-muted"], colorTokens["bg"]],
    ["muted text on surface", colorTokens["text-muted"], colorTokens["surface"]],
    ["on-accent on primary button", colorTokens["on-accent"], colorTokens["accent"]],
    ["on-accent on primary hover", colorTokens["on-accent"], colorTokens["accent-strong"]],
    ["on-accent on danger button", colorTokens["on-accent"], colorTokens["danger"]],
    ["on-accent on danger hover", colorTokens["on-accent"], colorTokens["danger-strong"]],
    ["status neutral", colorTokens["text"], colorTokens["neutral-soft"]],
    ["status info", colorTokens["accent-strong"], colorTokens["accent-soft"]],
    ["status success", colorTokens["success-strong"], colorTokens["success-soft"]],
    ["status warning", colorTokens["warning-strong"], colorTokens["warning-soft"]],
    ["status danger", colorTokens["danger-strong"], colorTokens["danger-soft"]],
    ["alert info title", colorTokens["accent-strong"], colorTokens["surface"]],
    ["alert success title", colorTokens["success-strong"], colorTokens["surface"]],
    ["alert warning title", colorTokens["warning-strong"], colorTokens["surface"]],
    ["alert danger title", colorTokens["danger-strong"], colorTokens["surface"]],
  ])("%s", (_name, foreground, background) => {
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
  });
});
