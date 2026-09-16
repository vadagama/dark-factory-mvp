import type { Meta, StoryObj } from "@storybook/react";
import { FormField } from "../patterns/FormField";
import { Input } from "./Input";

const meta = {
  title: "Components/Input",
  component: Input,
} satisfies Meta<typeof Input>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <FormField id="email" label="Email">
      <Input type="email" placeholder="you@example.com" />
    </FormField>
  ),
};

export const WithValue: Story = {
  render: () => (
    <FormField id="email-filled" label="Email" hint="Used for run notifications only.">
      <Input type="email" defaultValue="agent@example.com" />
    </FormField>
  ),
};

export const Invalid: Story = {
  render: () => (
    <FormField id="email-invalid" label="Email" error="Enter a valid email address.">
      <Input type="email" defaultValue="not-an-email" invalid />
    </FormField>
  ),
};

export const Disabled: Story = {
  render: () => (
    <FormField id="email-disabled" label="Email">
      <Input type="email" defaultValue="locked@example.com" disabled />
    </FormField>
  ),
};
