/** @type {import("stylelint").Config} */
// Stylelint config of the Small UIKit (T-042, ADR-014).
//
// Token discipline: color properties must reference var(--small-*) from the
// DTCG-generated src/tokens.css; hex/named colors in hand-written component
// CSS are blocked. The generated tokens file is the only place raw colors
// live, so it is exempted below (and guarded by the tokens drift test).
export default {
  extends: ["stylelint-config-standard"],
  ignoreFiles: ["node_modules/**", "dist/**", "coverage/**", "storybook-static/**"],
  rules: {
    "color-no-hex": true,
    "color-named": "never",
    // Token discipline: color values must come from var(--small-*) generated
    // from the DTCG token files. Value patterns are slash-wrapped regexes
    // (stylelint convention); plain strings would be compared literally.
    "declaration-property-value-allowed-list": {
      "/^(color|caret-color|text-decoration-color|column-rule-color|outline-color|fill|stroke)$/": [
        "/^var\\(--small-/",
        "currentColor",
      ],
      "/^(background-color|border-color)$/": [
        "/^var\\(--small-/",
        "currentColor",
        "transparent",
      ],
      "/^border(-(top|right|bottom|left))?$/": ["/^\\d+px (solid|dashed) var\\(--small-/"],
      "/^outline$/": ["/^\\d+px solid var\\(--small-/"],
    },
    // BEM class names of the kit: small-block__element--modifier with kebab
    // segments (e.g. small-empty-state__action, small-button--primary).
    "selector-class-pattern":
      "^[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*(?:__[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)?(?:--[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)?$",
    // Disabled: with BEM the rule fires on block vs block__element ordering
    // (e.g. .small-textarea before .small-textarea:disabled) and forces
    // artificial selector churn; specificity conflicts are covered by review.
    "no-descending-specificity": null,
  },
  overrides: [
    {
      // src/tokens.css is generated from tokens/*.tokens.json (DTCG) and is
      // the single source of raw color values; drift is caught by the vitest
      // tokens test, so the color rules are lifted here only.
      files: ["src/tokens.css"],
      rules: {
        // Generated from tokens/*.tokens.json (DTCG) by scripts/build-tokens.mjs
        // and guarded by the tokens drift test; stylelint cosmetics must not
        // fight the generator, so only structural rules apply here.
        "color-no-hex": null,
        "color-named": null,
        "color-function-notation": null,
        "color-function-alias-notation": null,
        "color-hex-length": null,
        "alpha-value-notation": null,
        "comment-empty-line-before": null,
        "value-keyword-case": null,
        "declaration-property-value-allowed-list": null,
      },
    },
  ],
};
