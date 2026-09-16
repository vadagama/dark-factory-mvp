import type { Preview } from "@storybook/react";
import "../src/tokens.css";
import "../src/styles.css";

// Every story renders in the same deterministic frame: centered layout on the
// surface color with the kit's base typography. The visual suite screenshots
// each story of this frame (tests/visual/uikit.spec.ts).
const preview: Preview = {
  parameters: {
    layout: "centered",
    options: {
      storySort: {
        order: ["Tokens", "Components", "Patterns"],
      },
    },
  },
};

export default preview;
