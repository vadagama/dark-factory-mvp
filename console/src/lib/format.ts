/**
 * Formatting helpers: no domain logic — just stable, testable presentation.
 */

const DATE_TIME_FORMAT = new Intl.DateTimeFormat("ru-RU", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "UTC",
});

export function formatDateTime(iso: string | null): string {
  if (!iso) {
    return "—";
  }
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return `${DATE_TIME_FORMAT.format(date)} UTC`;
}

export function formatCost(cost: string | null): string {
  if (cost === null) {
    return "—";
  }
  return `$${cost}`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat("ru-RU").format(value);
}

/** "acme/demo-service" rendered next to the provider badge. */
export function formatProduct(provider: string, slug: string): string {
  return `${provider}/${slug}`;
}

export function formatGate(gate: string): string {
  return gate.replaceAll("_", " ");
}

export function formatStage(stage: string): string {
  return stage.replaceAll("_", " ");
}
