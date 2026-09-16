import * as SelectPrimitive from "@radix-ui/react-select";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

/**
 * Select of the Small UIKit (T-042, ADR-014) — styled wrapper over the Radix
 * primitive (listbox semantics, typeahead and keyboard navigation are Radix's
 * responsibility). The control's a11y surface is the trigger button, so
 * `id` and `aria-*` props given to Select are forwarded there (FormField
 * relies on this when it clones the child with id/describedby).
 */
export type SelectProps = Omit<ComponentPropsWithoutRef<typeof SelectPrimitive.Root>, "children"> &
  Pick<
    ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>,
    "id" | "className" | "aria-label" | "aria-labelledby" | "aria-describedby" | "aria-invalid"
  > & {
    /** Placeholder shown on the closed trigger. */
    placeholder?: string;
    children: ReactNode;
  };

export function Select({
  placeholder,
  children,
  className,
  id,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledby,
  "aria-describedby": ariaDescribedby,
  "aria-invalid": ariaInvalid,
  ...rootProps
}: SelectProps) {
  return (
    <SelectPrimitive.Root {...rootProps}>
      <SelectPrimitive.Trigger
        id={id}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledby}
        aria-describedby={ariaDescribedby}
        aria-invalid={ariaInvalid}
        className={["small-select__trigger", className].filter(Boolean).join(" ")}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon className="small-select__icon">
          <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
            <path
              d="M2.5 4.5 6 8l3.5-3.5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content className="small-select__content" position="popper" sideOffset={4}>
          <SelectPrimitive.Viewport className="small-select__viewport">{children}</SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

export interface SelectItemProps extends ComponentPropsWithoutRef<typeof SelectPrimitive.Item> {
  children: ReactNode;
}

export function SelectItem({ className, children, ...rest }: SelectItemProps) {
  return (
    <SelectPrimitive.Item className={["small-select__item", className].filter(Boolean).join(" ")} {...rest}>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator className="small-select__item-indicator">
        <svg viewBox="0 0 12 12" aria-hidden="true" focusable="false">
          <path
            d="M2.5 6.5 5 9l4.5-6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}
