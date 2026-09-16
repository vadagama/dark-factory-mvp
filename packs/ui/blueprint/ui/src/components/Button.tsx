import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * Button of the Small UIKit (T-042, ADR-014). Pure React — no behavioral
 * primitive needed; styling comes from the DTCG token layer (src/styles.css).
 */
export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Visual weight; `primary` is the default call to action. */
  variant?: ButtonVariant;
  size?: ButtonSize;
  children?: ReactNode;
}

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary: "small-button--primary",
  secondary: "small-button--secondary",
  ghost: "small-button--ghost",
  danger: "small-button--danger",
};

const SIZE_CLASS: Record<ButtonSize, string> = {
  sm: "small-button--sm",
  md: "small-button--md",
};

export function Button({
  variant = "primary",
  size = "md",
  type = "button",
  className,
  ...rest
}: ButtonProps) {
  const classes = ["small-button", VARIANT_CLASS[variant], SIZE_CLASS[size], className]
    .filter(Boolean)
    .join(" ");
  return <button type={type} className={classes} {...rest} />;
}
