import { useId, type Ref } from "react";
import type { Evidence } from "../api/types";
import { displayValue, humanize } from "../lib/format";
import { KeyValueList } from "./KeyValues";
import { DateTime } from "./Time";

/** A clickable evidence citation: "Why did Aegis say this?" */
export function EvidenceRef({ id, onSelect }: { id: string; onSelect: (id: string) => void }) {
  return (
    <button
      type="button"
      className="evref"
      onClick={() => onSelect(id)}
      title="Why did Aegis say this? Show this evidence item"
      aria-label={`Show evidence ${id}`}
    >
      {id}
    </button>
  );
}

export function Citations({
  ids,
  onSelect,
  label = "Evidence",
  emptyText,
}: {
  ids: readonly string[] | null | undefined;
  onSelect: (id: string) => void;
  label?: string;
  emptyText?: string;
}) {
  const list = Array.from(new Set(ids ?? []));
  if (list.length === 0) return emptyText ? <p className="muted small">{emptyText}</p> : null;
  return (
    <div className="citations">
      <span className="citations-label">{label}:</span>
      {list.map((id) => (
        <EvidenceRef key={id} id={id} onSelect={onSelect} />
      ))}
    </div>
  );
}

function SourceRow({ source }: { source: Record<string, unknown> }) {
  const { provider, query, ...rest } = source;
  return (
    <li className="provenance-item">
      <div>
        <span className="provenance-provider">{displayValue(provider)}</span>
      </div>
      {query !== undefined ? <code className="provenance-query">{displayValue(query)}</code> : null}
      {Object.keys(rest).length ? <KeyValueList data={rest} className="kv-compact" /> : null}
    </li>
  );
}

/** One evidence item (FACT / OBSERVATION). Everything inside is untrusted text. */
export function EvidenceItem({
  evidence,
  expanded,
  onToggle,
  highlighted = false,
  itemRef,
}: {
  evidence: Evidence;
  expanded: boolean;
  onToggle: () => void;
  highlighted?: boolean;
  itemRef?: Ref<HTMLElement>;
}) {
  const bodyId = useId();
  return (
    <article
      className={`evidence${highlighted ? " evidence-highlight" : ""}`}
      ref={itemRef}
      tabIndex={-1}
      aria-label={`Evidence ${evidence.evidence_id}`}
    >
      <button
        type="button"
        className="evidence-toggle"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={onToggle}
      >
        <span className="evidence-meta">
          <span className="badge kind">{humanize(evidence.kind)}</span>
          {evidence.modality ? <span className="badge neutral">{evidence.modality}</span> : null}
          {evidence.service ? <span className="evidence-service">{evidence.service}</span> : null}
          {evidence.observed_at ? <DateTime value={evidence.observed_at} /> : null}
          <span className="evidence-chevron" aria-hidden="true">
            {expanded ? "▾" : "▸"}
          </span>
        </span>
        <span className="evidence-summary">{evidence.summary}</span>
      </button>
      {expanded ? (
        <div id={bodyId} className="evidence-body">
          <dl className="kv kv-compact">
            <div className="kv-row">
              <dt>Evidence ID</dt>
              <dd>
                <code className="selectable">{evidence.evidence_id}</code>
              </dd>
            </div>
          </dl>
          <h4 className="subhead">Provenance</h4>
          {evidence.sources.length ? (
            <ul className="provenance">
              {evidence.sources.map((s, i) => (
                <SourceRow key={i} source={s} />
              ))}
            </ul>
          ) : (
            <p className="muted small">No provenance recorded.</p>
          )}
          <h4 className="subhead">Attributes</h4>
          <KeyValueList data={evidence.attributes} emptyText="No attributes." className="kv-compact" />
        </div>
      ) : null}
    </article>
  );
}
