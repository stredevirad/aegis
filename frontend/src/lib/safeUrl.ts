// URL-scheme allowlist for links built from API data (security model C19).
//
// Only absolute http: and https: URLs without embedded credentials are allowed. Relative and
// protocol-relative URLs are rejected (they are parsed without a base URL, so they throw), as
// are javascript:, data:, vbscript:, file: and every other scheme. The normalised `href` of the
// parsed URL is returned — never the raw input string.

const MAX_URL_LENGTH = 4096;

export function safeUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const value = raw.trim();
  if (!value || value.length > MAX_URL_LENGTH) return null;
  // Control characters and whitespace inside a URL are never legitimate here.
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\s]/.test(value)) return null;
  // Require an explicit absolute http(s) scheme before parsing (rejects "//host", "/path").
  if (!/^https?:\/\//i.test(value)) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.username || url.password) return null;
  if (!url.hostname) return null;
  return url.href;
}
