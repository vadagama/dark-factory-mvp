import * as SwitchPrimitive from "@radix-ui/react-switch";
import type { ComponentPropsWithoutRef } from "react";

/**
 * Toggle of the Small UIKit (T-042, ADR-014) — a thin, styled wrapper over
 * the Radix primitive (role=switch, keyboard and pointer behavior are Radix's
 * responsibility). Always provide an accessible name: pair with a <label> or
 * pass aria-label (FormField does the former).
 */
export type SwitchProps = ComponentPropsWithoutRef<typeof SwitchPrimitive.Root>;

export function Switch({ className, ...rest }: SwitchProps) {
  return (
    <SwitchPrimitive.Root className={["small-switch", className].filter(Boolean).join(" ")} {...rest}>
      <SwitchPrimitive.Thumb className="small-switch__thumb" />
    </SwitchPrimitive.Root>
  );
}
