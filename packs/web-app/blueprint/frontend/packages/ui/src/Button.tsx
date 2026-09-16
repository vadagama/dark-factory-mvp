import type { ButtonHTMLAttributes } from "react";

import { tokens } from "./tokens";

type Variant = "primary" | "secondary";

const variantStyles: Record<Variant, ButtonHTMLAttributes<HTMLButtonElement>["style"]> = {
  primary: {
    background: tokens.color.accent,
    color: tokens.color.surface,
  },
  secondary: {
    background: tokens.color.surface,
    color: tokens.color.text,
  },
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

/** Minimal Button of the UIKit stub; T-071 replaces it with the real one. */
export function Button({ variant = "primary", style, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      style={{
        border: "1px solid transparent",
        borderRadius: tokens.radius.md,
        padding: `${tokens.space.sm} ${tokens.space.md}`,
        cursor: "pointer",
        ...variantStyles[variant],
        ...style,
      }}
      {...rest}
    />
  );
}
