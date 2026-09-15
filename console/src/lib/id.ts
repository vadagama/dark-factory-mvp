/**
 * Client-side id helpers.
 *
 * UUID v4 needs a fallback: jsdom (vitest) and non-secure origins may lack
 * `crypto.randomUUID`, which is only available in secure contexts.
 */

export function uuidV4(): string {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // RFC 4122 v4 fallback for non-secure contexts.
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Change id pattern matches the API convention: `chg_<12 hex chars>`. */
export function newChangeId(): string {
  return `chg_${uuidV4().replaceAll("-", "").slice(0, 12)}`;
}
