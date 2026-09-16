import type { InputHTMLAttributes } from "react";

/**
 * Single-line text input of the Small UIKit (T-042, ADR-014). Pure React.
 * Always pair it with a label (the FormField pattern does this and wires
 * aria-describedby for hint/error text).
 */
export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** Marks the control invalid for assistive tech; pairs with FormField error. */
  invalid?: boolean;
}

export function Input({ invalid = false, className, ...rest }: InputProps) {
  const classes = ["small-input", invalid ? "small-input--invalid" : "", className]
    .filter(Boolean)
    .join(" ");
  return <input className={classes} aria-invalid={invalid || undefined} {...rest} />;
}
