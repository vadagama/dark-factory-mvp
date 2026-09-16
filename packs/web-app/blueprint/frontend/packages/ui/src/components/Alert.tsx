import type { HTMLAttributes, ReactNode } from "react";

/**
 * Callout of the Small UIKit (T-042, ADR-014). Pure React; renders with
 * role="alert" so the message is announced without focus. The page must not
 * rely on color alone — every tone has an explicit text label.
 */
export type AlertTone = "info" | "success" | "warning" | "danger";

export type AlertProps = Omit<HTMLAttributes<HTMLDivElement>, "title"> & {
  tone?: AlertTone;
  /** Short, required heading of the message. */
  title: ReactNode;
  children?: ReactNode;
};

const TONE_CLASS: Record<AlertTone, string> = {
  info: "small-alert--info",
  success: "small-alert--success",
  warning: "small-alert--warning",
  danger: "small-alert--danger",
};

export function Alert({ tone = "info", title, children, className, ...rest }: AlertProps) {
  const classes = ["small-alert", TONE_CLASS[tone], className].filter(Boolean).join(" ");
  return (
    <div role="alert" className={classes} {...rest}>
      <p className="small-alert__title">{title}</p>
      {children ? <div className="small-alert__body">{children}</div> : null}
    </div>
  );
}
