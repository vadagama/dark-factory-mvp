/**
 * ESLint policy for products consuming the Small UIKit `@small/ui`
 * (T-042, ADR-014, pack `packs/ui`). Copy this file into the product
 * repository (e.g. `eslint/policy-small-ui.mjs`) and spread it into the flat
 * config: `import smallUiPolicy from "./eslint/policy-small-ui.mjs";`
 * then `...smallUiPolicy` among the config entries.
 *
 * Rules (ADR-014 п.3 — "запрет прямых импортов вне @small/ui"):
 * 1. Only the package root may be imported: `import { Button } from "@small/ui"`.
 *    Deep imports (`@small/ui/internal-thing`) reach into implementation
 *    details that are not covered by the kit's gates and visual baseline.
 * 2. Radix primitives (`@radix-ui/*`) are an implementation detail of the
 *    kit — product code imports composed components from `@small/ui` instead,
 *    so accessibility and theming stay consistent across products.
 *
 * The kit proves this policy blocks violations: `npm run test:gates` inside
 * `packs/ui/blueprint/ui` lints policy/fixtures with exactly this file.
 */
export default [
  {
    files: ["**/*.{js,jsx,ts,tsx}"],
    languageOptions: {
      parserOptions: {
        ecmaVersion: 2022,
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@small/ui/*"],
              message:
                "Import only from the package root: import { Button } from '@small/ui' (ADR-014).",
            },
            {
              group: ["@radix-ui/*"],
              message:
                "Radix primitives are an implementation detail of @small/ui; import composed components from '@small/ui' instead (ADR-014).",
            },
          ],
        },
      ],
    },
  },
];
