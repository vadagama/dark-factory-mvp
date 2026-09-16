import type { ReactNode } from "react";

/**
 * EmptyState pattern of the Small UIKit (T-042, ADR-014): what a section shows
 * when there is no data yet — a clear title (required), an optional
 * explanation and an optional action. Rendered with role="status" so screen
 * readers announce it when it replaces loaded content.
 */
export interface EmptyStateProps {
  title: ReactNode;
  description?: ReactNode;
  /** Call to action, e.g. a Button. */
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, action, className }: EmptyStateProps) {
  return (
    <div className={["small-empty-state", className].filter(Boolean).join(" ")} role="status">
      <p className="small-empty-state__title">{title}</p>
      {description ? <p className="small-empty-state__description">{description}</p> : null}
      {action ? <div className="small-empty-state__action">{action}</div> : null}
    </div>
  );
}
