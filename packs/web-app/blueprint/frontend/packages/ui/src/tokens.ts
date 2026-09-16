/**
 * Design tokens of the Small UIKit stub (ADR-014).
 * T-071 replaces this with the real token set (Radix + adapted shadcn).
 */
export const tokens = {
  color: {
    bg: "#f9fafb",
    surface: "#ffffff",
    text: "#111827",
    accent: "#2563eb",
    danger: "#dc2626",
  },
  radius: { md: "6px", lg: "10px" },
  space: { sm: "0.5rem", md: "0.75rem", lg: "1rem" },
} as const;

export type Tokens = typeof tokens;
