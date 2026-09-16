import { cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";

/**
 * FormField pattern of the Small UIKit (T-042, ADR-014): label + control +
 * optional hint + optional error, wired for assistive tech — the control gets
 * id, aria-describedby (hint and/or error) and aria-invalid automatically.
 * Pass exactly one control element as the child.
 */
export interface FormFieldProps {
  /** id of the control; also namespaces the hint/error ids. */
  id: string;
  label: ReactNode;
  /** Persistent helper text rendered under the control. */
  hint?: ReactNode;
  /** Validation message; when present the control is marked aria-invalid. */
  error?: ReactNode;
  required?: boolean;
  children: ReactElement;
  className?: string;
}

export function FormField({ id, label, hint, error, required = false, children, className }: FormFieldProps) {
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-error` : ""]
    .filter(Boolean)
    .join(" ");

  let control = children;
  if (isValidElement(children)) {
    const childProps = children.props as Record<string, unknown>;
    const extraDescription = typeof childProps["aria-describedby"] === "string" ? childProps["aria-describedby"] : "";
    control = cloneElement(children as ReactElement<Record<string, unknown>>, {
      id,
      required,
      "aria-describedby": [extraDescription, describedBy].filter(Boolean).join(" ") || undefined,
      "aria-invalid": error ? true : undefined,
    });
  }

  return (
    <div className={["small-form-field", className].filter(Boolean).join(" ")}>
      <label className="small-form-field__label" htmlFor={id}>
        {label}
        {required ? (
          <>
            {" "}
            <span className="small-visually-hidden">(required)</span>
            <span aria-hidden="true" className="small-form-field__required">
              *
            </span>
          </>
        ) : null}
      </label>
      {control}
      {hint ? (
        <p className="small-form-field__hint" id={`${id}-hint`}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="small-form-field__error" id={`${id}-error`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
