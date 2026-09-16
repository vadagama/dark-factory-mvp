import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "../components/Button";
import { EmptyState } from "./EmptyState";

const meta = {
  title: "Patterns/EmptyState",
  component: EmptyState,
} satisfies Meta<typeof EmptyState>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  args: {
    title: "No runs yet",
    description: "Start the first change and its runs will appear here.",
  },
};

export const WithAction: Story = {
  args: {
    title: "No runs yet",
    description: "Start the first change and its runs will appear here.",
    action: <Button size="sm">New change</Button>,
  },
};
