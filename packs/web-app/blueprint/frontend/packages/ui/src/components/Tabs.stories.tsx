import type { Meta, StoryObj } from "@storybook/react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./Tabs";

const meta = {
  title: "Components/Tabs",
  component: Tabs,
} satisfies Meta<typeof Tabs>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Tabs aria-label="Pipeline detail" defaultValue="spec">
      <TabsList>
        <TabsTrigger value="spec">Spec</TabsTrigger>
        <TabsTrigger value="tasks">Tasks</TabsTrigger>
        <TabsTrigger value="evidence">Evidence</TabsTrigger>
      </TabsList>
      <TabsContent value="spec">
        The change specification with acceptance criteria and scope decisions.
      </TabsContent>
      <TabsContent value="tasks">
        The ordered task graph with roles, DoD and dependencies.
      </TabsContent>
      <TabsContent value="evidence">
        Screenshots, logs and gate results attached to the final SHA.
      </TabsContent>
    </Tabs>
  ),
};

export const SecondTabActive: Story = {
  render: () => (
    <Tabs aria-label="Pipeline detail (second tab)" defaultValue="tasks">
      <TabsList>
        <TabsTrigger value="spec">Spec</TabsTrigger>
        <TabsTrigger value="tasks">Tasks</TabsTrigger>
        <TabsTrigger value="evidence">Evidence</TabsTrigger>
      </TabsList>
      <TabsContent value="spec">Spec content.</TabsContent>
      <TabsContent value="tasks">The ordered task graph is the active panel here.</TabsContent>
      <TabsContent value="evidence">Evidence content.</TabsContent>
    </Tabs>
  ),
};
