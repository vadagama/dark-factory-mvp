/**
 * Public surface of the Small UIKit (T-042, ADR-014). Products import only
 * from this root (`@small/ui`) — deep imports are blocked by the UIKit-policy
 * gate (policy/eslint-small-ui.mjs). Styles are imported separately:
 * `@small/ui/tokens.css` then `@small/ui/styles.css`.
 */

// Components (12).
export { Button, type ButtonProps, type ButtonSize, type ButtonVariant } from "./components/Button";
export { Input, type InputProps } from "./components/Input";
export { Textarea, type TextareaProps } from "./components/Textarea";
export { Badge, type BadgeProps, type BadgeVariant } from "./components/Badge";
export {
  Alert,
  type AlertProps,
  type AlertTone,
} from "./components/Alert";
export {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardBody,
  CardFooter,
  type CardProps,
} from "./components/Card";
export { Spinner, type SpinnerProps, type SpinnerSize } from "./components/Spinner";
export { Checkbox, type CheckboxProps } from "./components/Checkbox";
export { Switch, type SwitchProps } from "./components/Switch";
export { Select, SelectItem, type SelectProps, type SelectItemProps } from "./components/Select";
export {
  Dialog,
  DialogTrigger,
  DialogClose,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogBody,
  DialogFooter,
  type DialogContentProps,
  type DialogTitleProps,
  type DialogDescriptionProps,
  type DialogBodyProps,
  type DialogFooterProps,
} from "./components/Dialog";
export {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  type TabsListProps,
  type TabsTriggerProps,
  type TabsContentProps,
} from "./components/Tabs";

// Patterns (5) — compositions over the components above.
export { FormField, type FormFieldProps } from "./patterns/FormField";
export { StatusBadge, type StatusBadgeProps, type Status } from "./patterns/StatusBadge";
export { EmptyState, type EmptyStateProps } from "./patterns/EmptyState";
export { ConfirmDialog, type ConfirmDialogProps } from "./patterns/ConfirmDialog";
export { Toolbar, type ToolbarProps } from "./patterns/Toolbar";

// Design tokens (generated from tokens/*.tokens.json, DTCG).
export { tokens, type Tokens } from "./tokens";
