import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ComponentPropsWithoutRef } from "react";

/**
 * Tabs of the Small UIKit (T-042, ADR-014) — styled wrapper over the Radix
 * primitive (roving tabindex and arrow-key navigation are Radix's
 * responsibility). Give the tab list an accessible name via Tabs aria-label.
 */
export const Tabs = TabsPrimitive.Root;

export type TabsListProps = ComponentPropsWithoutRef<typeof TabsPrimitive.List>;

export function TabsList({ className, ...rest }: TabsListProps) {
  return (
    <TabsPrimitive.List
      className={["small-tabs__list", className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}

export type TabsTriggerProps = ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>;

export function TabsTrigger({ className, ...rest }: TabsTriggerProps) {
  return (
    <TabsPrimitive.Trigger
      className={["small-tabs__trigger", className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}

export type TabsContentProps = ComponentPropsWithoutRef<typeof TabsPrimitive.Content>;

export function TabsContent({ className, ...rest }: TabsContentProps) {
  return (
    <TabsPrimitive.Content
      className={["small-tabs__content", className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}
