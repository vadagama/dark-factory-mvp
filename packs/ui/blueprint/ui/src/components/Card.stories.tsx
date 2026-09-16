import type { Meta, StoryObj } from "@storybook/react";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { Card, CardBody, CardDescription, CardFooter, CardHeader, CardTitle } from "./Card";

const meta = {
  title: "Components/Card",
  component: Card,
} satisfies Meta<typeof Card>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Simple: Story = {
  render: () => (
    <Card>
      <CardBody>Plain surface for arbitrary content.</CardBody>
    </Card>
  ),
};

export const WithSections: Story = {
  render: () => (
    <Card>
      <CardHeader>
        <CardTitle>Run chg_smoke</CardTitle>
        <CardDescription>Construction stage, role develop</CardDescription>
      </CardHeader>
      <CardBody>
        The stage finished with 12 passing checks and one human gate pending review.
      </CardBody>
      <CardFooter>
        <Badge variant="soft">in review</Badge>
        <Button size="sm" variant="secondary">
          Open report
        </Button>
      </CardFooter>
    </Card>
  ),
};
