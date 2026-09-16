import type { Meta, StoryObj } from "@storybook/react";
import { Switch } from "./Switch";

const meta = {
  title: "Components/Switch",
  component: Switch,
} satisfies Meta<typeof Switch>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Off: Story = {
  render: () => (
    <div className="small-inline">
      <Switch id="switch-off" />
      <label className="small-label" htmlFor="switch-off">
        Visual regression on MR
      </label>
    </div>
  ),
};

export const On: Story = {
  render: () => (
    <div className="small-inline">
      <Switch id="switch-on" defaultChecked />
      <label className="small-label" htmlFor="switch-on">
        Visual regression on MR
      </label>
    </div>
  ),
};
