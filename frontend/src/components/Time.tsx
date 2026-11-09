import { formatDateTime, formatRelative, formatUtc, parseDate } from "../lib/format";

/** Local date-time; the UTC instant is in the title and the machine-readable attribute. */
export function DateTime({ value, relative = false }: { value: string | null | undefined; relative?: boolean }) {
  const d = parseDate(value);
  if (!d) return <span className="muted">—</span>;
  return (
    <time dateTime={d.toISOString()} title={`${formatUtc(value)} (UTC)`}>
      {formatDateTime(value)}
      {relative ? <span className="muted"> · {formatRelative(value)}</span> : null}
    </time>
  );
}

export function Relative({ value }: { value: string | null | undefined }) {
  const d = parseDate(value);
  if (!d) return <span className="muted">—</span>;
  return (
    <time dateTime={d.toISOString()} title={`${formatDateTime(value)} · ${formatUtc(value)} (UTC)`}>
      {formatRelative(value)}
    </time>
  );
}

export function WindowRange({ start, end }: { start: string; end: string }) {
  return (
    <span className="window-range">
      <DateTime value={start} /> <span aria-hidden="true">→</span>
      <span className="visually-hidden"> to </span> <DateTime value={end} />
    </span>
  );
}
