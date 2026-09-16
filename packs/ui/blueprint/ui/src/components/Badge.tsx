import type { HTMLAttributes, ReactNode } from "react";

/**
 * Compact status label of the Small UIKit (T-042, ADR-014). Pure React.
 * For domain statuses (order states, run results) prefer the StatusBadge
 * pattern, which maps a semantic status to this component's variants.
 */
export type BadgeVariant = "neutral" | "soft" | "outline";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  children: ReactNode;
}

const VARIANT_CLASS: Record<BadgeVariant, string> = {
  neutral: "small-badge--neutral",
  soft: "small-badge--soft",
  outline: "small-badge--outline",
};

export function Badge({ variant = "neutral", className, ...rest }: BadgeProps) {
  const classes = ["small-badge", VARIANT_CLASS[variant], className].filter(Boolean).join(" ");
  return <span className={classes} {...rest} />;
}
