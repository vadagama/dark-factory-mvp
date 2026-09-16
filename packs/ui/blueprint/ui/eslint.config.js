import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// ESLint config of the Small UIKit itself (T-042, ADR-014).
//
// The kit policy is part of the architecture (ADR-014):
// 1. Radix primitives are an implementation detail — `@radix-ui/*` may be
//    imported only inside `src/components/**`.
// 2. The public surface is the package root: no deep self-imports of
//    `@small/ui/...` (products get the same rule from policy/eslint-small-ui.mjs).
// The ready-to-copy product policy lives in policy/eslint-small-ui.mjs; its
// self-test (npm run test:gates) proves the rules actually block violations.
export default tseslint.config(
  {
    ignores: [
      "node_modules",
      "dist",
      "coverage",
      "storybook-static",
      "test-results",
      "playwright-report",
      // Fixtures intentionally violate the product policy; the self-test
      // (scripts/test-gates.mjs) lints them with that policy directly.
      "policy/fixtures/**",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
    },
  },
  {
    // Dependency-free Node scripts run outside the bundler.
    files: ["scripts/**/*.mjs"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.node },
    },
  },
  {
    // Outside components: no Radix at all, no self-imports.
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/components/**"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@small/ui", "@small/ui/*"],
              message: "The kit imports itself by relative paths; the public surface is src/index.ts.",
            },
            {
              group: ["@radix-ui/*"],
              message: "Radix primitives may be imported only inside src/components/** (ADR-014).",
            },
          ],
        },
      ],
    },
  },
  {
    // Inside components: Radix is allowed, self-imports are still banned.
    files: ["src/components/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@small/ui", "@small/ui/*"],
              message: "The kit imports itself by relative paths; the public surface is src/index.ts.",
            },
          ],
        },
      ],
    },
  },
);
