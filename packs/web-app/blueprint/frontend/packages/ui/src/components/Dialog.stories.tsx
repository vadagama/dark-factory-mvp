import type { Meta, StoryObj } from "@storybook/react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
  DialogTrigger,
} from "./Dialog";
import { Button } from "./Button";

const meta = {
  title: "Components/Dialog",
  component: Dialog,
} satisfies Meta<typeof Dialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Open: Story = {
  render: () => (
    <Dialog open onOpenChange={() => {}}>
      <DialogContent>
        <DialogTitle>Publish the spec artifact</DialogTitle>
        <DialogDescription>
          The built Storybook is attached to the run as UI evidence.
        </DialogDescription>
        <DialogFooter>
          <Button variant="secondary">Not now</Button>
          <Button>Publish</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
};

export const WithTrigger: Story = {
  render: () => (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="secondary">Open dialog</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Nothing is rendered yet</DialogTitle>
        <DialogDescription>
          This story shows the closed trigger; the Open story pins the open state.
        </DialogDescription>
      </DialogContent>
    </Dialog>
  ),
};
