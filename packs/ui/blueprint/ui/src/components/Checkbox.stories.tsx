import type { Meta, StoryObj } from "@storybook/react";
import { Checkbox } from "./Checkbox";

const meta = {
  title: "Components/Checkbox",
  component: Checkbox,
} satisfies Meta<typeof Checkbox>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Unchecked: Story = {
  render: () => (
    <div className="small-inline">
      <Checkbox id="checkbox-off" />
      <label className="small-label" htmlFor="checkbox-off">
        Auto-merge low-risk changes
      </label>
    </div>
  ),
};

export const Checked: Story = {
  render: () => (
    <div className="small-inline">
      <Checkbox id="checkbox-on" defaultChecked />
      <label className="small-label" htmlFor="checkbox-on">
        Auto-merge low-risk changes
      </label>
    </div>
  ),
};

export const Disabled: Story = {
  render: () => (
    <div className="small-inline">
      <Checkbox id="checkbox-disabled" defaultChecked disabled />
      <label className="small-label" htmlFor="checkbox-disabled">
        Policy locked by ADR-011
      </label>
    </div>
  ),
};
