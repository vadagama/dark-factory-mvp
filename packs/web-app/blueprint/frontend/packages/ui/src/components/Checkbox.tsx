import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import type { ComponentPropsWithoutRef } from "react";

/**
 * Checkbox of the Small UIKit (T-042, ADR-014) — a thin, styled wrapper over
 * the Radix primitive (keyboard interaction, form association and
 * role=checkbox are Radix's responsibility). Always provide an accessible
 * name: pair with a <label> or pass aria-label (FormField does the former).
 */
export type CheckboxProps = ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>;

export function Checkbox({ className, ...rest }: CheckboxProps) {
  return (
    <CheckboxPrimitive.Root className={["small-checkbox", className].filter(Boolean).join(" ")} {...rest}>
      <CheckboxPrimitive.Indicator className="small-checkbox__indicator">
        <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
          <path
            d="M2.5 6.5 5 9l4.5-6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
