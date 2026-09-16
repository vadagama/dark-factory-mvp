import type { HTMLAttributes, ReactNode } from "react";

/**
 * Toolbar pattern of the Small UIKit (T-042, ADR-014): a labeled row of
 * related controls (actions, filters, view switchers). Plain React with
 * role="toolbar" — the composition requirement is that interactive children
 * come from the kit's public surface.
 */
export interface ToolbarProps extends HTMLAttributes<HTMLDivElement> {
  /** Accessible name of the toolbar (announced by assistive tech). */
  "aria-label": string;
  children: ReactNode;
}

export function Toolbar({ className, ...rest }: ToolbarProps) {
  return (
    <div role="toolbar" className={["small-toolbar", className].filter(Boolean).join(" ")} {...rest} />
  );
}
