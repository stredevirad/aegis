import type { IncidentStatus, InvestigationStatus } from "../api/types";

/** A random token for Idempotency-Key headers. `crypto.randomUUID` needs a secure context;
 * `getRandomValues` does not. Matches the API's pattern ^[A-Za-z0-9._:-]{1,200}$. */
export function randomToken(): string {
  const c: Crypto | undefined = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    try {
      return c.randomUUID();
    } catch {
      // not a secure context: fall back below
    }
  }
  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === "function") {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Only same-origin absolute paths are accepted as a post-login destination (no open redirect). */
export function sanitizeReturnTo(value: string | null | undefined): string {
  if (!value || value.length > 500) return "/";
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\\]/.test(value)) return "/";
  if (value === "/login" || value.startsWith("/login?") || value.startsWith("/auth/")) return "/";
  return value;
}

/** Manual lifecycle transitions allowed by the API (backend incidents.TRANSITIONS). The server
 * is authoritative; a 409 invalid_transition is still handled. */
export const INCIDENT_TRANSITIONS: Record<IncidentStatus, IncidentStatus[]> = {
  open: ["investigating", "resolved", "closed"],
  investigating: ["open", "resolved", "closed"],
  resolved: ["open", "closed"],
  closed: [],
};

export const TRANSITION_VERBS: Record<IncidentStatus, string> = {
  open: "Reopen",
  investigating: "Mark investigating",
  resolved: "Resolve",
  closed: "Close",
};

export const TERMINAL_INVESTIGATION: ReadonlySet<InvestigationStatus> = new Set([
  "completed",
  "failed",
  "cancelled",
]);

export function isActiveInvestigation(status: string): boolean {
  return status === "queued" || status === "running";
}

export function sourceDescription(source: Record<string, unknown>): string {
  const kind = typeof source.kind === "string" ? source.kind : "unknown";
  if (kind === "scenario") {
    const scenario = typeof source.scenario === "string" ? source.scenario : "?";
    return `Scenario ${scenario}`;
  }
  if (kind === "live") return "Live telemetry";
  return kind;
}
