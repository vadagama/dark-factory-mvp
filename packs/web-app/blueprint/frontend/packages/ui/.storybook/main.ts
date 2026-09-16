import type { StorybookConfig } from "@storybook/react-vite";

// Storybook is the executable UI spec of the Small UIKit (T-042, ADR-014 п.2):
// tokens, components and patterns are documented by stories, built in CI
// (`npm run storybook:build`) and published as an artifact of the factory
// pipeline. The visual regression suite screenshots every story of the built
// output (tests/visual/uikit.spec.ts).
const config: StorybookConfig = {
  framework: "@storybook/react-vite",
  stories: ["../src/**/*.stories.@(ts|tsx)"],
  addons: ["@storybook/addon-docs"],
  docs: {
    autodocs: "tag",
  },
};

export default config;
