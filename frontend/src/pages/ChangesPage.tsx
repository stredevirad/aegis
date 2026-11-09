import { useId, useMemo, useState, type FormEvent } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api, type QueryParams } from "../api/client";
import { qk } from "../api/queries";
import { CHANGE_KINDS, type ChangeEvent, type Page } from "../api/types";
import { LoadMore } from "../components/Controls";
import { EmptyState, ErrorNotice, Loading } from "../components/Feedback";
import { KeyValueList } from "../components/KeyValues";
import { PageHeader } from "../components/Layout";
import { DateTime } from "../components/Time";
import { humanize, isoToLocalInput, localInputToIso } from "../lib/format";

interface Filters {
  service: string;
  environment: string;
  kind: string[];
  since: string;
  until: string;
}

function readFilters(sp: URLSearchParams): Filters {
  return {
    service: sp.get("service") ?? "",
    environment: sp.get("environment") ?? "",
    kind: sp.getAll("kind").filter((k) => (CHANGE_KINDS as readonly string[]).includes(k)),
    since: sp.get("since") ?? "",
    until: sp.get("until") ?? "",
  };
}

function FilterForm({ initial, onApply }: { initial: Filters; onApply: (f: Filters) => void }) {
  const [f, setF] = useState(initial);
  const ids = { service: useId(), env: useId(), since: useId(), until: useId() };
  function submit(e: FormEvent) {
    e.preventDefault();
    onApply({ ...f, service: f.service.trim(), environment: f.environment.trim() });
  }
  return (
    <form className="filters" onSubmit={submit} aria-label="Change event filters">
      <div className="field">
        <label htmlFor={ids.service}>Service</label>
        <input id={ids.service} value={f.service} maxLength={200} onChange={(e) => setF({ ...f, service: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={ids.env}>Environment</label>
        <input id={ids.env} value={f.environment} maxLength={100} onChange={(e) => setF({ ...f, environment: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={ids.since}>At or after (local time)</label>
        <input
          id={ids.since}
          type="datetime-local"
          value={isoToLocalInput(f.since)}
          onChange={(e) => setF({ ...f, since: localInputToIso(e.target.value) ?? "" })}
        />
      </div>
      <div className="field">
        <label htmlFor={ids.until}>Before (local time)</label>
        <input
          id={ids.until}
          type="datetime-local"
          value={isoToLocalInput(f.until)}
          onChange={(e) => setF({ ...f, until: localInputToIso(e.target.value) ?? "" })}
        />
      </div>
      <fieldset className="checkbox-group">
        <legend>Kind</legend>
        {CHANGE_KINDS.map((k) => (
          <label key={k} className="check-label">
            <input
              type="checkbox"
              checked={f.kind.includes(k)}
              onChange={(e) => setF({ ...f, kind: e.target.checked ? [...f.kind, k] : f.kind.filter((x) => x !== k) })}
            />
            {humanize(k)}
          </label>
        ))}
      </fieldset>
      <div className="filter-actions">
        <button type="submit" className="btn btn-primary">
          Apply filters
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            const empty = readFilters(new URLSearchParams());
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

export function ChangesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readFilters(searchParams), [searchParams]);
  const params: QueryParams = useMemo(() => ({ ...filters, limit: 50 }), [filters]);
  const q = useInfiniteQuery({
    queryKey: qk.changes(params),
    queryFn: ({ pageParam, signal }) =>
      api.get<Page<ChangeEvent>>("/api/v1/change-events", { ...params, cursor: pageParam }, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];

  function apply(f: Filters) {
    const sp = new URLSearchParams();
    if (f.service) sp.set("service", f.service);
    if (f.environment) sp.set("environment", f.environment);
    f.kind.forEach((k) => sp.append("kind", k));
    if (f.since) sp.set("since", f.since);
    if (f.until) sp.set("until", f.until);
    setSearchParams(sp);
  }

  return (
    <div className="page">
      <PageHeader title="Change events" />
      <p className="muted small">
        Deployments, configuration changes, feature flags and rollouts reported to Aegis. They are used as
        evidence when a change precedes an anomaly.
      </p>
      <details className="disclosure filters-disclosure" open>
        <summary>Filters</summary>
        <FilterForm key={searchParams.toString()} initial={filters} onApply={apply} />
      </details>
      {q.isPending ? (
        <Loading label="Loading change events…" />
      ) : q.isError ? (
        <ErrorNotice error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState title={searchParams.toString() ? "No change events match these filters" : "No change events yet"}>
          <p>Change events arrive through POST /api/v1/change-events with an API key that has the ingest:changes scope.</p>
        </EmptyState>
      ) : (
        <>
          <div className="table-wrap">
            <table className="table">
              <caption className="visually-hidden">Change events</caption>
              <thead>
                <tr>
                  <th scope="col">Time</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Service</th>
                  <th scope="col">Environment</th>
                  <th scope="col">Version</th>
                  <th scope="col">Summary</th>
                  <th scope="col">Actor</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <DateTime value={c.ts} />
                    </td>
                    <td>
                      <span className="badge neutral">{humanize(c.kind)}</span>
                    </td>
                    <td>{c.service ?? "—"}</td>
                    <td>{c.environment ?? "—"}</td>
                    <td className="mono small">{c.version ?? "—"}</td>
                    <td className="cell-title">
                      <div>{c.summary}</div>
                      {Object.keys(c.scope).length || Object.keys(c.metadata).length ? (
                        <details className="disclosure disclosure-inline">
                          <summary>Scope and metadata</summary>
                          <h4 className="subhead">Scope</h4>
                          <KeyValueList data={c.scope} className="kv-compact" />
                          <h4 className="subhead">Metadata</h4>
                          <KeyValueList data={c.metadata} className="kv-compact" />
                          <p className="muted small">
                            Received <DateTime value={c.received_at} />
                            {c.external_id ? ` · external ID ${c.external_id}` : ""}
                          </p>
                        </details>
                      ) : null}
                    </td>
                    <td>{c.actor ?? "—"}</td>
                    <td>{c.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <LoadMore
            hasNextPage={q.hasNextPage}
            isFetchingNextPage={q.isFetchingNextPage}
            onLoadMore={() => void q.fetchNextPage()}
            shown={items.length}
            noun="change events"
          />
        </>
      )}
    </div>
  );
}
