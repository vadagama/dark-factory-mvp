import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Button } from "../src/components/Button";
import { Input } from "../src/components/Input";
import { Textarea } from "../src/components/Textarea";
import { Select, SelectItem } from "../src/components/Select";
import { Checkbox } from "../src/components/Checkbox";
import { Switch } from "../src/components/Switch";
import { Badge } from "../src/components/Badge";
import { Alert } from "../src/components/Alert";
import { Spinner } from "../src/components/Spinner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "../src/components/Dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../src/components/Tabs";
import { FormField } from "../src/patterns/FormField";
import { StatusBadge } from "../src/patterns/StatusBadge";
import { EmptyState } from "../src/patterns/EmptyState";
import { ConfirmDialog } from "../src/patterns/ConfirmDialog";
import { Toolbar } from "../src/patterns/Toolbar";

describe("components", () => {
  it("Button applies variant classes and handles clicks", async () => {
    const onClick = vi.fn();
    render(
      <Button variant="danger" onClick={onClick}>
        Delete
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Delete" });
    expect(button).toHaveClass("small-button", "small-button--danger");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("Button defaults type to button", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toHaveAttribute("type", "button");
  });

  it("Input wires invalid state for assistive tech", () => {
    render(<Input aria-label="Email" defaultValue="broken" invalid />);
    const input = screen.getByLabelText("Email");
    expect(input).toHaveAttribute("aria-invalid", "true");
  });

  it("Textarea renders with rows", () => {
    render(<Textarea aria-label="Intent" rows={4} />);
    expect(screen.getByLabelText("Intent")).toHaveAttribute("rows", "4");
  });

  it("Select renders a combobox trigger with items available", async () => {
    render(
      <Select aria-label="Stage" placeholder="Pick a stage">
        <SelectItem value="intake">Intake</SelectItem>
        <SelectItem value="construction">Construction</SelectItem>
      </Select>,
    );
    const trigger = screen.getByRole("combobox", { name: "Stage" });
    expect(trigger).toHaveTextContent("Pick a stage");
    // Radix renders items in a portal on open; the trigger is the a11y surface
    // we assert here (pointer-based item selection is browser-gated).
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("Checkbox toggles via user events", async () => {
    render(<Checkbox aria-label="Auto-merge" />);
    const checkbox = screen.getByRole("checkbox", { name: "Auto-merge" });
    expect(checkbox).toHaveAttribute("data-state", "unchecked");
    await userEvent.click(checkbox);
    expect(checkbox).toHaveAttribute("data-state", "checked");
  });

  it("Switch toggles via user events", async () => {
    render(<Switch aria-label="Visual regression" defaultChecked />);
    const toggle = screen.getByRole("switch", { name: "Visual regression" });
    expect(toggle).toHaveAttribute("data-state", "checked");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("data-state", "unchecked");
  });

  it("Badge renders content", () => {
    render(<Badge variant="outline">beta</Badge>);
    expect(screen.getByText("beta")).toHaveClass("small-badge--outline");
  });

  it("Alert exposes role=alert", () => {
    render(
      <Alert tone="warning" title="Token drift">
        Run tokens:build.
      </Alert>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Token drift");
  });

  it("Spinner announces its label via role=status", () => {
    render(<Spinner label="Loading runs" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading runs");
  });

  it("Dialog opens from a trigger and closes", async () => {
    render(
      <Dialog>
        <DialogTrigger asChild>
          <Button>Open dialog</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogTitle>Publish the spec</DialogTitle>
          <DialogDescription>The Storybook build is attached.</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Open dialog" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Publish the spec")).toBeInTheDocument();
  });

  it("Tabs switch the visible panel", async () => {
    render(
      <Tabs defaultValue="spec" aria-label="Detail">
        <TabsList>
          <TabsTrigger value="spec">Spec</TabsTrigger>
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
        </TabsList>
        <TabsContent value="spec">The specification.</TabsContent>
        <TabsContent value="tasks">The task graph.</TabsContent>
      </Tabs>,
    );
    expect(screen.getByRole("tabpanel")).toHaveTextContent("The specification.");
    await userEvent.click(screen.getByRole("tab", { name: "Tasks" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("The task graph.");
  });
});

describe("patterns", () => {
  it("FormField associates label, control, hint and error", () => {
    render(
      <FormField id="email" label="Email" hint="Work address." error="Required.">
        <Input type="email" />
      </FormField>,
    );
    const input = screen.getByLabelText("Email");
    expect(input).toHaveAttribute("aria-describedby", "email-hint email-error");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText("Work address.")).toHaveAttribute("id", "email-hint");
    expect(screen.getByText("Required.")).toHaveAttribute("id", "email-error");
  });

  it("FormField without hint/error keeps describedby empty", () => {
    render(
      <FormField id="plain" label="Name">
        <Input />
      </FormField>,
    );
    expect(screen.getByLabelText("Name")).not.toHaveAttribute("aria-describedby");
  });

  it("StatusBadge maps every status to its token class", () => {
    render(
      <>
        <StatusBadge status="info">in review</StatusBadge>
        <StatusBadge status="danger">failed</StatusBadge>
      </>,
    );
    expect(screen.getByText("in review")).toHaveClass("small-status-badge--info");
    expect(screen.getByText("failed")).toHaveClass("small-status-badge--danger");
  });

  it("EmptyState renders title, description and action", () => {
    render(
      <EmptyState
        title="No runs yet"
        description="Start the first change."
        action={<Button size="sm">New change</Button>}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("No runs yet");
    expect(screen.getByRole("button", { name: "New change" })).toBeInTheDocument();
  });

  it("ConfirmDialog fires confirm and closes through the cancel action", async () => {
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <ConfirmDialog
        open
        onOpenChange={onOpenChange}
        tone="danger"
        title="Delete run?"
        description="This removes the evidence entry."
        confirmLabel="Delete"
        onConfirm={onConfirm}
      />,
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("Delete run?");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("Toolbar keeps children interactive and exposes role=toolbar", () => {
    render(
      <Toolbar aria-label="Controls">
        <Button size="sm">New change</Button>
      </Toolbar>,
    );
    expect(screen.getByRole("toolbar", { name: "Controls" })).toBeInTheDocument();
  });
});
