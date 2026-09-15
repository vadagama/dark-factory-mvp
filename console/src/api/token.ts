/**
 * Operator token storage (ADR-021 p.4): localStorage only, never logged,
 * never sent to reads. SC-008 explicitly allows losing this cache — the only
 * loss is the token itself, which is re-entered on the settings screen.
 */

const TOKEN_KEY = "dark_factory_operator_token";

export function getToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

/**
 * Masked display (ADR-021 p.4): only the suffix is shown — never the value
 * itself, so screenshots and screen shares do not leak the secret.
 */
export function maskToken(token: string): string {
  if (token.length <= 4) {
    return "••••";
  }
  return `••••••••${token.slice(-4)}`;
}
