import type { TextareaHTMLAttributes } from "react";

/**
 * Multi-line text input of the Small UIKit (T-042, ADR-014). Pure React.
 * Always pair it with a label (the FormField pattern does this).
 */
export interface TextareaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** Marks the control invalid for assistive tech; pairs with FormField error. */
  invalid?: boolean;
}

export function Textarea({ invalid = false, className, ...rest }: TextareaProps) {
  const classes = ["small-textarea", invalid ? "small-textarea--invalid" : "", className]
    .filter(Boolean)
    .join(" ");
  return <textarea className={classes} aria-invalid={invalid || undefined} {...rest} />;
}
