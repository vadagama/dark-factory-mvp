import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "./Button";

const meta = {
  title: "Components/Button",
  component: Button,
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Primary: Story = {
  args: { children: "Save changes" },
};

export const Secondary: Story = {
  args: { variant: "secondary", children: "Open documentation" },
};

export const Ghost: Story = {
  args: { variant: "ghost", children: "Dismiss" },
};

export const Danger: Story = {
  args: { variant: "danger", children: "Delete run" },
};

export const Small: Story = {
  args: { size: "sm", children: "Compact action" },
};

export const Disabled: Story = {
  args: { children: "Unavailable", disabled: true },
};
