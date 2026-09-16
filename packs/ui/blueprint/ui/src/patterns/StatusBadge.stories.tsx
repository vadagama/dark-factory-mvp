import type { Meta, StoryObj } from "@storybook/react";
import { StatusBadge } from "./StatusBadge";

const meta = {
  title: "Patterns/StatusBadge",
  component: StatusBadge,
  // Defaults for the required props; the stories below render explicit states.
  args: { status: "neutral", children: "status" },
} satisfies Meta<typeof StatusBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllStatuses: Story = {
  render: () => (
    <div className="small-inline">
      <StatusBadge status="neutral">draft</StatusBadge>
      <StatusBadge status="info">in review</StatusBadge>
      <StatusBadge status="success">merged</StatusBadge>
      <StatusBadge status="warning">rework</StatusBadge>
      <StatusBadge status="danger">failed</StatusBadge>
    </div>
  ),
};

export const Single: Story = {
  args: { status: "success", children: "merged" },
};
