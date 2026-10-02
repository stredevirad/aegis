import { useId, useMemo, useState, type FormEvent } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type QueryParams } from "../api/client";
import { fetchInvestigation, investigationPath, qk } from "../api/queries";
import { EVIDENCE_KINDS, MODALITIES, type Evidence, type Page } from "../api/types";
import { InvestigationStatusBadge, SyntheticBadge } from "../components/Badge";
import { LoadMore } from "../components/Controls";
import { EvidenceItem } from "../components/Evidence";
import { EmptyState, ErrorNotice, Loading } from "../components/Feedback";
import { PageHeader } from "../components/Layout";
import { humanize } from "../lib/format";

interface Filters {
  kind: string[];
  modality: string[];
  service: string;
}

function readFilters(sp: URLSearchParams): Filters {
  return {
    kind: sp.getAll("kind").filter((k) => (EVIDENCE_KINDS as readonly string[]).includes(k)),
    modality: sp.getAll("modality").filter((m) => (MODALITIES as readonly string[]).includes(m)),
    service: sp.get("service") ?? "",
  };
}

function MultiSelect({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: readonly string[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <fieldset className="checkbox-group">
      <legend>{legend}</legend>
      {options.map((o) => (
        <label key={o} className="check-label">
          <input
            type="checkbox"
            checked={value.includes(o)}
            onChange={(e) => onChange(e.target.checked ? [...value, o] : value.filter((v) => v !== o))}
          />
          {humanize(o)}
        </label>
      ))}
    </fieldset>
  );
}

function FilterForm({ initial, onApply }: { initial: Filters; onApply: (f: Filters) => void }) {
  const [f, setF] = useState(initial);
  const serviceId = useId();
  function submit(e: FormEvent) {
    e.preventDefault();
    onApply({ ...f, service: f.service.trim() });
  }
  return (
    <form className="filters" onSubmit={submit} aria-label="Evidence filters">
      <MultiSelect legend="Kind" options={EVIDENCE_KINDS} value={f.kind} onChange={(kind) => setF({ ...f, kind })} />
      <MultiSelect legend="Modality" options={MODALITIES} value={f.modality} onChange={(modality) => setF({ ...f, modality })} />
      <div className="field">
        <label htmlFor={serviceId}>Service</label>
        <input id={serviceId} value={f.service} maxLength={200} onChange={(e) => setF({ ...f, service: e.target.value })} />
      </div>
      <div className="filter-actions">
        <button type="submit" className="btn btn-primary">
          Apply filters
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            const empty = { kind: [], modality: [], service: "" };
            setF(empty);
            onApply(empty);
          }}
        >
          Reset
        </button>
      </div>
    </form>
  );
}

export function EvidenceExplorerPage() {
  const { investigationId = "" } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readFilters(searchParams), [searchParams]);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const invQ = useQuery({ queryKey: qk.investigation(investigationId), queryFn: () => fetchInvestigation(investigationId) });
  const params: QueryParams = useMemo(
    () => ({ kind: filters.kind, modality: filters.modality, service: filters.service, limit: 100 }),
    [filters],
  );
  const q = useInfiniteQuery({
    queryKey: qk.evidencePage(investigationId, params),
    queryFn: ({ pageParam, signal }) =>
      api.get<Page<Evidence>>(`${investigationPath(investigationId)}/evidence`, { ...params, cursor: pageParam }, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  function apply(f: Filters) {
    const sp = new URLSearchParams();
    f.kind.forEach((k) => sp.append("kind", k));
    f.modality.forEach((m) => sp.append("modality", m));
    if (f.service) sp.set("service", f.service);
    setSearchParams(sp);
  }

  return (
    <div className="page">
      <PageHeader
        kicker={
          <Link to={`/investigations/${encodeURIComponent(investigationId)}`}>Back to the investigation</Link>
        }
        title="Evidence explorer"
      >
        {invQ.data ? <InvestigationStatusBadge status={invQ.data.status} /> : null}
        {invQ.data?.synthetic ? <SyntheticBadge /> : null}
        <span className="muted small mono">{investigationId}</span>
      </PageHeader>
      <p className="muted small">
        FACT / OBSERVATION: evidence extracted from the investigation's telemetry snapshot, each with its provenance.
        Summaries are derived from telemetry and are untrusted text.
      </p>
      <details className="disclosure filters-disclosure" open>
        <summary>Filters</summary>
        <FilterForm key={searchParams.toString()} initial={filters} onApply={apply} />
      </details>
      {q.isPending ? (
        <Loading label="Loading evidence…" />
      ) : q.isError ? (
        <ErrorNotice error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={searchParams.toString() ? "No evidence matches these filters" : "No evidence for this investigation"}>
          {invQ.data && invQ.data.status !== "completed" ? (
            <p>Evidence is stored when the analysis finishes (status: {invQ.data.status}).</p>
          ) : null}
        </EmptyState>
      ) : (
        <>
          <div className="evidence-list">
            {items.map((e) => (
              <EvidenceItem
                key={e.evidence_id}
                evidence={e}
                expanded={open.has(e.evidence_id)}
                onToggle={() => toggle(e.evidence_id)}
              />
            ))}
          </div>
          <LoadMore
            hasNextPage={q.hasNextPage}
            isFetchingNextPage={q.isFetchingNextPage}
            onLoadMore={() => void q.fetchNextPage()}
            shown={items.length}
            noun="evidence items"
          />
        </>
      )}
    </div>
  );
}
