import type { Meta, StoryObj } from "@storybook/react";
import { Alert } from "./Alert";

const meta = {
  title: "Components/Alert",
  component: Alert,
} satisfies Meta<typeof Alert>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Info: Story = {
  args: { tone: "info", title: "Baseline update requested" },
};

export const Success: Story = {
  args: {
    tone: "success",
    title: "All gates passed",
    children: "typecheck, lint, tests and the visual suite are green on the final SHA.",
  },
};

export const Warning: Story = {
  args: {
    tone: "warning",
    title: "Token drift detected",
    children: "src/tokens.css does not match tokens/*.tokens.json — run npm run tokens:build.",
  },
};

export const Danger: Story = {
  args: {
    tone: "danger",
    title: "Merge blocked",
    children: "The change touches a trust boundary and requires a security review.",
  },
};
