import type { Meta, StoryObj } from "@storybook/react";
import { Badge } from "./Badge";

const meta = {
  title: "Components/Badge",
  component: Badge,
} satisfies Meta<typeof Badge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Neutral: Story = {
  args: { children: "v0.1.0" },
};

export const Soft: Story = {
  args: { variant: "soft", children: "new" },
};

export const Outline: Story = {
  args: { variant: "outline", children: "beta" },
};
