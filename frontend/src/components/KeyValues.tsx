import { displayValue } from "../lib/format";

/** Key/value pairs from untrusted data (labels, annotations, features, attributes), as text. */
export function KeyValueList({
  data,
  emptyText = "None",
  className,
}: {
  data: Record<string, unknown> | null | undefined;
  emptyText?: string;
  className?: string;
}) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) return <p className="muted small">{emptyText}</p>;
  return (
    <dl className={`kv ${className ?? ""}`}>
      {entries.map(([k, v]) => (
        <div className="kv-row" key={k}>
          <dt>{k}</dt>
          <dd>{displayValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Inline chips "key=value" for compact table cells. */
export function LabelChips({ data, max = 6 }: { data: Record<string, unknown> | null | undefined; max?: number }) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) return <span className="muted">—</span>;
  const shown = entries.slice(0, max);
  return (
    <span className="chips">
      {shown.map(([k, v]) => (
        <span className="chip" key={k}>
          {k}={displayValue(v)}
        </span>
      ))}
      {entries.length > max ? <span className="muted small">+{entries.length - max} more</span> : null}
    </span>
  );
}
