import type { HTMLAttributes, ReactNode } from "react";

/**
 * Card surface of the Small UIKit (T-042, ADR-014). Pure React; shadcn-style
 * compound API (Card > CardHeader/CardTitle/CardDescription/CardBody/CardFooter).
 */
export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children?: ReactNode;
}

export function Card({ className, ...rest }: CardProps) {
  return <div className={["small-card", className].filter(Boolean).join(" ")} {...rest} />;
}

export function CardHeader({ className, ...rest }: CardProps) {
  return <div className={["small-card__header", className].filter(Boolean).join(" ")} {...rest} />;
}

export function CardTitle({ className, ...rest }: CardProps) {
  return (
    <h3 className={["small-card__title", className].filter(Boolean).join(" ")} {...rest} />
  );
}

export function CardDescription({ className, ...rest }: CardProps) {
  return (
    <p className={["small-card__description", className].filter(Boolean).join(" ")} {...rest} />
  );
}

export function CardBody({ className, ...rest }: CardProps) {
  return <div className={["small-card__body", className].filter(Boolean).join(" ")} {...rest} />;
}

export function CardFooter({ className, ...rest }: CardProps) {
  return <div className={["small-card__footer", className].filter(Boolean).join(" ")} {...rest} />;
}
