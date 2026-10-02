// Formatting helpers. They only transform values for display; React renders the results as text.

const dateTime = new Intl.DateTimeFormat(undefined, {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const timeOnly = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateTime(value: string | null | undefined): string {
  const d = parseDate(value);
  return d ? dateTime.format(d) : "—";
}

export function formatTime(value: string | null | undefined): string {
  const d = parseDate(value);
  return d ? timeOnly.format(d) : "—";
}

export function formatUtc(value: string | null | undefined): string {
  const d = parseDate(value);
  return d ? d.toISOString().replace(".000Z", "Z") : "";
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  const negative = ms < 0;
  let s = Math.round(Math.abs(ms) / 1000);
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  s -= m * 60;
  const parts = h ? [`${h} h`, `${m} min`] : m ? [`${m} min`, `${s} s`] : [`${s} s`];
  return (negative ? "-" : "") + parts.join(" ");
}

export function durationBetween(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  const a = parseDate(start);
  const b = parseDate(end);
  if (!a || !b) return "—";
  return formatDuration(b.getTime() - a.getTime());
}

export function formatRelative(value: string | null | undefined, now: number = Date.now()): string {
  const d = parseDate(value);
  if (!d) return "—";
  const diff = Math.round((now - d.getTime()) / 1000);
  const future = diff < 0;
  const s = Math.abs(diff);
  let text: string;
  if (s < 45) text = `${s} s`;
  else if (s < 3600) text = `${Math.round(s / 60)} min`;
  else if (s < 86400) text = `${Math.round(s / 3600)} h`;
  else text = `${Math.round(s / 86400)} d`;
  return future ? `in ${text}` : `${text} ago`;
}

/** "change_induced:deployment" → "change induced: deployment". */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/_/g, " ").replace(/:/g, ": ");
}

export function capitalize(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

/** Render any JSON value as a single display string (objects as compact JSON). */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : formatNumber(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function formatNumber(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  if (Number.isInteger(value)) return value.toLocaleString();
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 1 : abs >= 1 ? 2 : 4;
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function shortHash(value: string | null | undefined, length = 12): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

/** `<input type="datetime-local">` value (local time) → timezone-aware ISO string. */
export function localInputToIso(value: string): string | undefined {
  if (!value) return undefined;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

/** Timezone-aware ISO string → `<input type="datetime-local">` value in local time. */
export function isoToLocalInput(value: string | null | undefined): string {
  const d = parseDate(value);
  if (!d) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
    `T${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}
