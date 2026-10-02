import { useId, useMemo, useState, type FormEvent } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type QueryParams } from "../api/client";
import { qk } from "../api/queries";
import {
  INCIDENT_STATUSES,
  SEVERITIES,
  type Incident,
  type IncidentCreate,
  type Page,
  type Severity,
} from "../api/types";
import { useCan } from "../auth/context";
import { P } from "../auth/permissions";
import { IncidentStatusBadge, SeverityBadge } from "../components/Badge";
import { LoadMore } from "../components/Controls";
import { EmptyState, ErrorNotice, Loading } from "../components/Feedback";
import { PageHeader, Panel } from "../components/Layout";
import { DateTime, Relative } from "../components/Time";
import { capitalize, isoToLocalInput, localInputToIso } from "../lib/format";

const SORTS = [
  { value: "-created_at", label: "Created (newest first)" },
  { value: "created_at", label: "Created (oldest first)" },
  { value: "-started_at", label: "Started (newest first)" },
  { value: "started_at", label: "Started (oldest first)" },
];

interface Filters {
  status: string[];
  severity: string[];
  service: string;
  environment: string;
  since: string;
  until: string;
  q: string;
  sort: string;
}

function readFilters(sp: URLSearchParams): Filters {
  const sort = sp.get("sort") ?? "-created_at";
  return {
    status: sp.getAll("status").filter((s) => (INCIDENT_STATUSES as string[]).includes(s)),
    severity: sp.getAll("severity").filter((s) => (SEVERITIES as string[]).includes(s)),
    service: sp.get("service") ?? "",
    environment: sp.get("environment") ?? "",
    since: sp.get("since") ?? "",
    until: sp.get("until") ?? "",
    q: sp.get("q") ?? "",
    sort: SORTS.some((s) => s.value === sort) ? sort : "-created_at",
  };
}

function writeFilters(f: Filters): URLSearchParams {
  const sp = new URLSearchParams();
  f.status.forEach((s) => sp.append("status", s));
  f.severity.forEach((s) => sp.append("severity", s));
  for (const key of ["service", "environment", "since", "until", "q"] as const) {
    if (f[key]) sp.set(key, f[key]);
  }
  if (f.sort !== "-created_at") sp.set("sort", f.sort);
  return sp;
}

function CheckboxGroup({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: readonly string[];
  value: string[];
  onChange: (next: string[]) => void;
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
          {capitalize(o)}
        </label>
      ))}
    </fieldset>
  );
}

function FilterForm({ initial, onApply }: { initial: Filters; onApply: (f: Filters) => void }) {
  const [f, setF] = useState<Filters>(initial);
  const ids = { q: useId(), service: useId(), env: useId(), since: useId(), until: useId(), sort: useId() };
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => setF((prev) => ({ ...prev, [key]: value }));
  function submit(e: FormEvent) {
    e.preventDefault();
    onApply({ ...f, q: f.q.trim(), service: f.service.trim(), environment: f.environment.trim() });
  }
  return (
    <form className="filters" onSubmit={submit} aria-label="Incident filters">
      <div className="field">
        <label htmlFor={ids.q}>Title contains</label>
        <input id={ids.q} type="search" value={f.q} maxLength={200} onChange={(e) => set("q", e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.service}>Service</label>
        <input id={ids.service} value={f.service} maxLength={200} onChange={(e) => set("service", e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.env}>Environment</label>
        <input id={ids.env} value={f.environment} maxLength={100} onChange={(e) => set("environment", e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={ids.since}>Started at or after (local time)</label>
        <input
          id={ids.since}
          type="datetime-local"
          value={isoToLocalInput(f.since)}
          onChange={(e) => set("since", localInputToIso(e.target.value) ?? "")}
        />
      </div>
      <div className="field">
        <label htmlFor={ids.until}>Started before (local time)</label>
        <input
          id={ids.until}
          type="datetime-local"
          value={isoToLocalInput(f.until)}
          onChange={(e) => set("until", localInputToIso(e.target.value) ?? "")}
        />
      </div>
      <div className="field">
        <label htmlFor={ids.sort}>Sort</label>
        <select id={ids.sort} value={f.sort} onChange={(e) => set("sort", e.target.value)}>
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </div>
      <CheckboxGroup legend="Status" options={INCIDENT_STATUSES} value={f.status} onChange={(v) => set("status", v)} />
      <CheckboxGroup legend="Severity" options={SEVERITIES} value={f.severity} onChange={(v) => set("severity", v)} />
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

function CreateIncidentForm({ onCancel }: { onCancel: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const ids = { title: useId(), severity: useId(), service: useId(), env: useId(), desc: useId(), started: useId() };
  const [form, setForm] = useState({
    title: "",
    severity: "high" as Severity,
    service: "",
    environment: "",
    description: "",
    started: "",
  });
  const create = useMutation({
    mutationFn: (body: IncidentCreate) => api.post<Incident>("/api/v1/incidents", body),
    onSuccess: (incident) => {
      void qc.invalidateQueries({ queryKey: ["incidents"] });
      navigate(`/incidents/${encodeURIComponent(incident.id)}`);
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    const body: IncidentCreate = { title: form.title.trim(), severity: form.severity };
    if (form.description.trim()) body.description = form.description.trim();
    if (form.service.trim()) body.service = form.service.trim();
    if (form.environment.trim()) body.environment = form.environment.trim();
    const started = localInputToIso(form.started);
    if (started) body.started_at = started;
    create.mutate(body);
  }
  return (
    <Panel title="New incident">
      <form className="form-grid" onSubmit={submit}>
        <div className="field field-wide">
          <label htmlFor={ids.title}>Title</label>
          <input
            id={ids.title}
            required
            maxLength={300}
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
          />
        </div>
        <div className="field">
          <label htmlFor={ids.severity}>Severity</label>
          <select
            id={ids.severity}
            value={form.severity}
            onChange={(e) => setForm({ ...form, severity: e.target.value as Severity })}
          >
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {capitalize(s)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={ids.service}>Service (optional)</label>
          <input id={ids.service} maxLength={200} value={form.service} onChange={(e) => setForm({ ...form, service: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor={ids.env}>Environment (optional)</label>
          <input id={ids.env} maxLength={100} value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })} />
        </div>
        <div className="field">
          <label htmlFor={ids.started}>Started at (local time, optional)</label>
          <input id={ids.started} type="datetime-local" value={form.started} onChange={(e) => setForm({ ...form, started: e.target.value })} />
        </div>
        <div className="field field-wide">
          <label htmlFor={ids.desc}>Description (optional)</label>
          <textarea id={ids.desc} rows={3} maxLength={4000} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        </div>
        <div className="form-actions field-wide">
          <button type="submit" className="btn btn-primary" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create incident"}
          </button>
          <button type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
        </div>
        {create.isError ? (
          <div className="field-wide">
            <ErrorNotice error={create.error} title="The incident was not created" />
          </div>
        ) : null}
      </form>
    </Panel>
  );
}

export function IncidentTable({ incidents, caption }: { incidents: Incident[]; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Severity</th>
            <th scope="col">Title</th>
            <th scope="col">Status</th>
            <th scope="col">Service</th>
            <th scope="col">Environment</th>
            <th scope="col">Started</th>
            <th scope="col" className="num">
              Alerts
            </th>
            <th scope="col">Last alert</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={i.id}>
              <td>
                <SeverityBadge severity={i.severity} />
              </td>
              <td className="cell-title">
                <Link to={`/incidents/${encodeURIComponent(i.id)}`}>{i.title}</Link>
              </td>
              <td>
                <IncidentStatusBadge status={i.status} />
              </td>
              <td>{i.service ?? <span className="muted">—</span>}</td>
              <td>{i.environment ?? <span className="muted">—</span>}</td>
              <td>
                <DateTime value={i.started_at} />
              </td>
              <td className="num">{i.alert_count}</td>
              <td>
                <Relative value={i.last_alert_at} />
              </td>
              <td>{i.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function IncidentsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const can = useCan();
  const [creating, setCreating] = useState(false);
  const filters = useMemo(() => readFilters(searchParams), [searchParams]);
  const params: QueryParams = useMemo(
    () => ({
      status: filters.status,
      severity: filters.severity,
      service: filters.service,
      environment: filters.environment,
      since: filters.since,
      until: filters.until,
      q: filters.q,
      sort: filters.sort,
      limit: 50,
    }),
    [filters],
  );
  const q = useInfiniteQuery({
    queryKey: qk.incidents(params),
    queryFn: ({ pageParam, signal }) =>
      api.get<Page<Incident>>("/api/v1/incidents", { ...params, cursor: pageParam }, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const incidents = q.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = searchParams.toString() !== "";

  return (
    <div className="page">
      <PageHeader
        title="Incidents"
        actions={
          can(P.incidentWrite) && !creating ? (
            <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
              New incident
            </button>
          ) : null
        }
      />
      {creating ? <CreateIncidentForm onCancel={() => setCreating(false)} /> : null}
      <details className="disclosure filters-disclosure" open>
        <summary>Filters{filtered ? " (active)" : ""}</summary>
        <FilterForm key={searchParams.toString()} initial={filters} onApply={(f) => setSearchParams(writeFilters(f))} />
      </details>
      {q.isPending ? (
        <Loading label="Loading incidents…" />
      ) : q.isError ? (
        <ErrorNotice error={q.error} title="Incidents could not be loaded" onRetry={() => void q.refetch()} />
      ) : incidents.length === 0 ? (
        <EmptyState title={filtered ? "No incidents match these filters" : "No incidents yet"}>
          {filtered ? (
            <p>Change or reset the filters.</p>
          ) : (
            <p>Incidents are created from Alertmanager or generic webhooks (an API key with the ingest:alerts scope), or manually.</p>
          )}
        </EmptyState>
      ) : (
        <>
          <IncidentTable incidents={incidents} caption="Incidents" />
          <LoadMore
            hasNextPage={q.hasNextPage}
            isFetchingNextPage={q.isFetchingNextPage}
            onLoadMore={() => void q.fetchNextPage()}
            shown={incidents.length}
            noun="incidents"
          />
        </>
      )}
    </div>
  );
}
