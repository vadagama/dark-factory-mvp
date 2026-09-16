import type { HTMLAttributes, ReactNode } from "react";

/**
 * StatusBadge pattern of the Small UIKit (T-042, ADR-014): maps a semantic
 * status to the token pair (soft background + strong text) chosen for WCAG
 * contrast (asserted in tests/tokens.test.ts). The status word itself is the
 * accessible content, so color never carries meaning alone.
 */
export type Status = "neutral" | "info" | "success" | "warning" | "danger";

export interface StatusBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  status: Status;
  children: ReactNode;
}

const STATUS_CLASS: Record<Status, string> = {
  neutral: "small-status-badge--neutral",
  info: "small-status-badge--info",
  success: "small-status-badge--success",
  warning: "small-status-badge--warning",
  danger: "small-status-badge--danger",
};

export function StatusBadge({ status, className, ...rest }: StatusBadgeProps) {
  return (
    <span
      className={["small-status-badge", STATUS_CLASS[status], className].filter(Boolean).join(" ")}
      data-status={status}
      {...rest}
    />
  );
}
