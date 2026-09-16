import * as DialogPrimitive from "@radix-ui/react-dialog";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

/**
 * Modal dialog of the Small UIKit (T-042, ADR-014) — styled wrapper over the
 * Radix primitive (focus trap, escape handling and modal semantics are
 * Radix's responsibility). Every dialog MUST render DialogTitle; pair it with
 * DialogDescription so the content is announced (ConfirmDialog composes this
 * correctly for the standard confirm/cancel case).
 */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export interface DialogContentProps extends ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  children: ReactNode;
}

export function DialogContent({ className, children, ...rest }: DialogContentProps) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="small-dialog__overlay" />
      <DialogPrimitive.Content className={["small-dialog__content", className].filter(Boolean).join(" ")} {...rest}>
        {children}
        <DialogPrimitive.Close asChild>
          <button type="button" className="small-dialog__close" aria-label="Close">
            <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
              <path
                d="m3 3 6 6m0-6-6 6"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export type DialogTitleProps = ComponentPropsWithoutRef<typeof DialogPrimitive.Title>;

export function DialogTitle({ className, ...rest }: DialogTitleProps) {
  return (
    <DialogPrimitive.Title
      className={["small-dialog__title", className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}

export type DialogDescriptionProps = ComponentPropsWithoutRef<
  typeof DialogPrimitive.Description
>;

export function DialogDescription({ className, ...rest }: DialogDescriptionProps) {
  return (
    <DialogPrimitive.Description
      className={["small-dialog__description", className].filter(Boolean).join(" ")}
      {...rest}
    />
  );
}

export interface DialogBodyProps {
  children: ReactNode;
  className?: string;
}

/** Content area between the header and the footer actions. */
export function DialogBody({ children, className }: DialogBodyProps) {
  return <div className={["small-dialog__body", className].filter(Boolean).join(" ")}>{children}</div>;
}

export interface DialogFooterProps {
  children: ReactNode;
  className?: string;
}

/** Footer actions row (Cancel/Confirm composition — see ConfirmDialog). */
export function DialogFooter({ children, className }: DialogFooterProps) {
  return (
    <div className={["small-dialog__footer", className].filter(Boolean).join(" ")}>{children}</div>
  );
}
