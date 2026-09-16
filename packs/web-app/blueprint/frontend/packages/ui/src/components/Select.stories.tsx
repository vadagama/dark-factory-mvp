import type { Meta, StoryObj } from "@storybook/react";
import { Select, SelectItem } from "./Select";

const meta = {
  title: "Components/Select",
  component: Select,
  // Defaults for the required props; the stories below render explicit items.
  // `children` must be covered here so Story objects do not require `args`.
  args: { children: null },
} satisfies Meta<typeof Select>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Select aria-label="Stage" placeholder="Pick a stage">
      <SelectItem value="intake">Intake</SelectItem>
      <SelectItem value="construction">Construction</SelectItem>
      <SelectItem value="verification">Verification</SelectItem>
    </Select>
  ),
};

export const WithValue: Story = {
  render: () => (
    <Select aria-label="Stage with value" defaultValue="construction">
      <SelectItem value="intake">Intake</SelectItem>
      <SelectItem value="construction">Construction</SelectItem>
      <SelectItem value="verification">Verification</SelectItem>
    </Select>
  ),
};
