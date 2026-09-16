import type { Meta, StoryObj } from "@storybook/react";
import { FormField } from "./FormField";
import { Input } from "../components/Input";

const meta = {
  title: "Patterns/FormField",
  component: FormField,
  // Defaults for the required props; the stories below render explicit states.
  // `children` must be covered here so Story objects do not require `args`.
  args: { id: "form-field", label: "Label", children: <Input /> },
} satisfies Meta<typeof FormField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithHint: Story = {
  render: () => (
    <FormField
      id="field-hint"
      label="Run token budget"
      hint="Upper bound in tokens for the whole attempt; unknown spend reserves conservatively."
    >
      <Input type="number" defaultValue={200000} />
    </FormField>
  ),
};

export const Required: Story = {
  render: () => (
    <FormField id="field-required" label="Product name" required>
      <Input defaultValue="example-product" />
    </FormField>
  ),
};

export const WithError: Story = {
  render: () => (
    <FormField id="field-error" label="Deadline" error="Deadline must be in the future.">
      <Input type="datetime-local" defaultValue="2020-01-01T00:00" invalid />
    </FormField>
  ),
};
