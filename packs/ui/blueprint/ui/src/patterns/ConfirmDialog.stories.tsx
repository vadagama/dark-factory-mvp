import type { Meta, StoryObj } from "@storybook/react";
import { ConfirmDialog } from "./ConfirmDialog";

const meta = {
  title: "Patterns/ConfirmDialog",
  component: ConfirmDialog,
} satisfies Meta<typeof ConfirmDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const OpenDefault: Story = {
  args: {
    open: true,
    onOpenChange: () => {},
    title: "Retire the bootstrap phase?",
    description:
      "Spec Kit artifacts stay as evidence; new changes start as ChangeSets in Native SDD Core.",
  },
};

export const OpenDanger: Story = {
  args: {
    open: true,
    onOpenChange: () => {},
    tone: "danger",
    title: "Delete run rsv_smoke?",
    description: "The run record is immutable; deleting it removes the evidence index entry.",
    confirmLabel: "Delete",
  },
};
