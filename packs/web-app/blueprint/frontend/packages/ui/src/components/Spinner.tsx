/**
 * Indeterminate progress indicator of the Small UIKit (T-042, ADR-014).
 * Pure React. Renders role="status" with an accessible label; the animation
 * stops under prefers-reduced-motion (and in the visual suite).
 */
export type SpinnerSize = "sm" | "md";

export interface SpinnerProps {
  /** Accessible name announced to assistive tech (default: "Loading"). */
  label?: string;
  size?: SpinnerSize;
  className?: string;
}

const SIZE_CLASS: Record<SpinnerSize, string> = {
  sm: "small-spinner--sm",
  md: "small-spinner--md",
};

export function Spinner({ label = "Loading", size = "md", className }: SpinnerProps) {
  const classes = ["small-spinner", SIZE_CLASS[size], className].filter(Boolean).join(" ");
  return (
    <span role="status" className={classes}>
      <span className="small-visually-hidden">{label}</span>
    </span>
  );
}
