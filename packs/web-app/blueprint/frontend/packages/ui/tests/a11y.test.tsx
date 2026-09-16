import { describe, expect, it } from "vitest";
import axe from "axe-core";
import { render } from "@testing-library/react";
import { Button } from "../src/components/Button";
import { Input } from "../src/components/Input";
import { Textarea } from "../src/components/Textarea";
import { Select, SelectItem } from "../src/components/Select";
import { Checkbox } from "../src/components/Checkbox";
import { Switch } from "../src/components/Switch";
import { Badge } from "../src/components/Badge";
import { Alert } from "../src/components/Alert";
import { Card, CardBody, CardDescription, CardHeader, CardTitle } from "../src/components/Card";
import { Spinner } from "../src/components/Spinner";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../src/components/Dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../src/components/Tabs";
import { FormField } from "../src/patterns/FormField";
import { StatusBadge } from "../src/patterns/StatusBadge";
import { EmptyState } from "../src/patterns/EmptyState";
import { ConfirmDialog } from "../src/patterns/ConfirmDialog";
import { Toolbar } from "../src/patterns/Toolbar";
import { expectNoAxeViolations } from "./a11y";

/**
 * A11y gate (T-042, ADR-014 п.3): every component and every pattern is
 * exercised in its required states and must produce ZERO axe violations under
 * the wcag2a/wcag2aa tags. axe in jsdom covers only the automatically
 * checkable subset of WCAG 2.2 AA — the non-automatable criteria live in
 * docs/wcag22-aa-checklist.md and are confirmed by a human.
 */
describe("a11y: components render without WCAG 2.0/2.1 A/AA violations", () => {
  it("Button", async () => {
    await expectNoAxeViolations(<Button>Save changes</Button>, "Button primary");
    await expectNoAxeViolations(
      <Button variant="ghost" disabled>
        Dismiss
      </Button>,
      "Button ghost disabled",
    );
  });

  it("Input", async () => {
    await expectNoAxeViolations(
      <FormField id="a11y-input" label="Email">
        <Input type="email" />
      </FormField>,
      "Input with label",
    );
  });

  it("Textarea", async () => {
    await expectNoAxeViolations(
      <FormField id="a11y-textarea" label="Intent">
        <Textarea rows={3} />
      </FormField>,
      "Textarea with label",
    );
  });

  it("Select", async () => {
    await expectNoAxeViolations(
      <Select aria-label="Stage" placeholder="Pick a stage">
        <SelectItem value="intake">Intake</SelectItem>
        <SelectItem value="construction">Construction</SelectItem>
      </Select>,
      "Select with aria-label",
    );
  });

  it("Checkbox", async () => {
    await expectNoAxeViolations(
      <Checkbox aria-label="Auto-merge" defaultChecked />,
      "Checkbox with aria-label",
    );
  });

  it("Switch", async () => {
    await expectNoAxeViolations(<Switch aria-label="Visual regression" />, "Switch with aria-label");
  });

  it("Badge", async () => {
    await expectNoAxeViolations(<Badge variant="soft">new</Badge>, "Badge soft");
  });

  it("Alert", async () => {
    await expectNoAxeViolations(
      <Alert tone="danger" title="Merge blocked">
        The change requires a security review.
      </Alert>,
      "Alert danger",
    );
  });

  it("Card", async () => {
    await expectNoAxeViolations(
      <Card>
        <CardHeader>
          <CardTitle>Run chg_smoke</CardTitle>
          <CardDescription>Construction stage</CardDescription>
        </CardHeader>
        <CardBody>Twelve passing checks.</CardBody>
      </Card>,
      "Card with sections",
    );
  });

  it("Spinner", async () => {
    await expectNoAxeViolations(<Spinner label="Loading runs" />, "Spinner with label");
  });

  it("Dialog (open, with title and description)", async () => {
    render(
      <Dialog open onOpenChange={() => {}}>
        <DialogContent>
          <DialogTitle>Publish the spec artifact</DialogTitle>
          <DialogDescription>The built Storybook is attached to the run.</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    const results = await axe.run(document.body, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    });
    const summary = results.violations.map((violation) => violation.id).join(", ");
    expect(summary, "Dialog open").toBe("");
  });

  it("Tabs", async () => {
    await expectNoAxeViolations(
      <Tabs aria-label="Pipeline detail" defaultValue="spec">
        <TabsList>
          <TabsTrigger value="spec">Spec</TabsTrigger>
          <TabsTrigger value="tasks">Tasks</TabsTrigger>
        </TabsList>
        <TabsContent value="spec">The change specification.</TabsContent>
        <TabsContent value="tasks">The ordered task graph.</TabsContent>
      </Tabs>,
      "Tabs with label",
    );
  });
});

describe("a11y: patterns render without WCAG 2.0/2.1 A/AA violations", () => {
  it("FormField with hint", async () => {
    await expectNoAxeViolations(
      <FormField id="a11y-hint" label="Budget" hint="Upper bound for the attempt.">
        <Input type="number" />
      </FormField>,
      "FormField with hint",
    );
  });

  it("FormField with error", async () => {
    await expectNoAxeViolations(
      <FormField id="a11y-error" label="Deadline" error="Deadline must be in the future.">
        <Input type="datetime-local" invalid />
      </FormField>,
      "FormField with error",
    );
  });

  it("StatusBadge (all five statuses)", async () => {
    for (const status of ["neutral", "info", "success", "warning", "danger"] as const) {
      await expectNoAxeViolations(
        <StatusBadge status={status}>{status}</StatusBadge>,
        `StatusBadge ${status}`,
      );
    }
  });

  it("EmptyState", async () => {
    await expectNoAxeViolations(
      <EmptyState title="No runs yet" description="Start the first change." />,
      "EmptyState",
    );
  });

  it("ConfirmDialog (open, danger)", async () => {
    render(
      <ConfirmDialog
        open
        onOpenChange={() => {}}
        tone="danger"
        title="Delete run?"
        description="The run record is immutable; deleting removes the evidence entry."
      />,
    );
    const results = await axe.run(document.body, {
      runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] },
    });
    const summary = results.violations.map((violation) => violation.id).join(", ");
    expect(summary, "ConfirmDialog open").toBe("");
  });

  it("Toolbar", async () => {
    await expectNoAxeViolations(
      <Toolbar aria-label="Run list controls">
        <Button size="sm" variant="secondary">
          Rerun failed
        </Button>
        <Button size="sm">New change</Button>
      </Toolbar>,
      "Toolbar with actions",
    );
  });
});
