import type { Meta, StoryObj } from "@storybook/react";
import { Spinner } from "./Spinner";

const meta = {
  title: "Components/Spinner",
  component: Spinner,
} satisfies Meta<typeof Spinner>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <div className="small-inline">
      <Spinner label="Loading runs" />
      <span className="small-label">Loading runs…</span>
    </div>
  ),
};

export const Small: Story = {
  render: () => (
    <div className="small-inline">
      <Spinner size="sm" label="Refreshing" />
      <span className="small-label">Refreshing…</span>
    </div>
  ),
};
