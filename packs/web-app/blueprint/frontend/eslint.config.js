import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";
import smallUiPolicy from "./packages/ui/policy/eslint-small-ui.mjs";

export default tseslint.config(
  {
    ignores: [
      "dist",
      "coverage",
      "node_modules",
      // The Small UIKit is a workspace package with its own ESLint config and
      // gates (npm run ui:lint / ui:gates): Radix imports are its implementation
      // detail, and its violation fixtures must never be linted from here. The
      // product policy below still guards all app code against them.
      "packages/ui",
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
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // The blueprint renders pages/components only; a warn keeps helpers
      // co-located without letting component modules silently export the world.
      "react-refresh/only-export-components": "warn",
    },
  },
  // Small UIKit policy (ADR-014, packs/ui): app code imports the kit only from
  // the package root (@small/ui, no deep imports) and never imports Radix
  // primitives directly. The policy self-blocks violations — proven by
  // `npm run ui:gates` inside packages/ui.
  ...smallUiPolicy,
  // Prettier owns formatting; this config only disables conflicting ESLint rules.
  prettier,
);
