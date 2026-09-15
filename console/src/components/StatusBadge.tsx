import type { StatusTone } from "../lib/statusTone";

interface StatusBadgeProps {
  label: string;
  tone?: StatusTone;
}

/** Colored wire-status chip; the label is the raw wire value (stable). */
export function StatusBadge({ label, tone = "neutral" }: StatusBadgeProps) {
  return <span className={`badge badge--${tone}`}>{label.replaceAll("_", " ")}</span>;
}
