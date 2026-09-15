/**
 * Local console settings (ADR-021 p.4/p.5): the API base URL override.
 * The token has its own module (token.ts); both live in localStorage and
 * their loss is acceptable (SC-008).
 */

const BASE_URL_KEY = "dark_factory_api_base_url";

/** Default: same origin "/api/v1" — vite proxy in dev, nginx proxy in the chart. */
export const DEFAULT_API_BASE_URL = "/api/v1";

export function getApiBaseUrl(): string {
  return window.localStorage.getItem(BASE_URL_KEY) ?? DEFAULT_API_BASE_URL;
}

export function setApiBaseUrl(url: string): void {
  const trimmed = url.trim();
  if (!trimmed || trimmed === DEFAULT_API_BASE_URL) {
    window.localStorage.removeItem(BASE_URL_KEY);
  } else {
    window.localStorage.setItem(BASE_URL_KEY, trimmed);
  }
}
