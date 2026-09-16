import type { Meta, StoryObj } from "@storybook/react";
import { Button } from "../components/Button";
import { Input } from "../components/Input";
import { Toolbar } from "./Toolbar";

const meta = {
  title: "Patterns/Toolbar",
  component: Toolbar,
  // Defaults for the required props; the story below renders explicit actions.
  // `children` must be covered here so Story objects do not require `args`.
  args: { "aria-label": "Toolbar", children: null },
} satisfies Meta<typeof Toolbar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithActions: Story = {
  render: () => (
    <Toolbar aria-label="Run list controls">
      <Input type="search" placeholder="Filter by change id" />
      <Button size="sm" variant="secondary">
        Rerun failed
      </Button>
      <Button size="sm">New change</Button>
    </Toolbar>
  ),
};
