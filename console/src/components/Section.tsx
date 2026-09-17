import type { ReactNode } from "react";

interface SectionProps {
  title: string;
  description?: string;
  children: ReactNode;
}

/** Card-like content section used by every screen. */
export function Section({ title, description, children }: SectionProps) {
  return (
    <section className="card">
      <h2 className="card__title">{title}</h2>
      {description ? <p className="card__description">{description}</p> : null}
      {children}
    </section>
  );
}

export function LoadingState({ label = "Загрузка…" }: { label?: string }) {
  return (
    <p className="state state--loading" role="status">
      {label}
    </p>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <p className="state state--empty" role="status">
      {label}
    </p>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <p className="state state--error" role="alert">
      Ошибка: {message}
    </p>
  );
}

export function Notice({
  tone,
  children,
}: {
  tone: "success" | "error" | "warning";
  children: ReactNode;
}) {
  return (
    <p className={`notice notice--${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}
