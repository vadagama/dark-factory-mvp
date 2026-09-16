import type { Meta, StoryObj } from "@storybook/react";
import { FormField } from "../patterns/FormField";
import { Textarea } from "./Textarea";

const meta = {
  title: "Components/Textarea",
  component: Textarea,
} satisfies Meta<typeof Textarea>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <FormField id="intent" label="Change intent">
      <Textarea rows={4} placeholder="Describe what should change and why" />
    </FormField>
  ),
};

export const WithHint: Story = {
  render: () => (
    <FormField id="intent-hint" label="Change intent" hint="Markdown is supported.">
      <Textarea rows={4} defaultValue="Migrate the health check to the new adapter." />
    </FormField>
  ),
};

export const Invalid: Story = {
  render: () => (
    <FormField id="intent-invalid" label="Change intent" error="Intent cannot be empty.">
      <Textarea rows={4} invalid />
    </FormField>
  ),
};
