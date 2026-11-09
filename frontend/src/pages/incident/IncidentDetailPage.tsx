import { useId, useState, type FormEvent } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, isNotFound, seg } from "../../api/client";
import { qk } from "../../api/queries";
import {
  SEVERITIES,
  type Alert,
  type AuditEvent,
  type Incident,
  type IncidentDetail,
  type IncidentStatus,
  type IncidentUpdate,
  type Investigation,
  type Page,
  type Severity,
} from "../../api/types";
import { useCan } from "../../auth/context";
import { P } from "../../auth/permissions";
import { AuditTable } from "../../components/AuditTable";
import {
  IncidentStatusBadge,
  InvestigationStatusBadge,
  SeverityBadge,
  SupportBadge,
  SyntheticBadge,
  VerdictBadge,
} from "../../components/Badge";
import { LoadMore } from "../../components/Controls";
import { EmptyState, ErrorNotice, Loading } from "../../components/Feedback";
import { KeyValueList } from "../../components/KeyValues";
import { PageHeader, Panel } from "../../components/Layout";
import { DateTime } from "../../components/Time";
import { capitalize } from "../../lib/format";
import { INCIDENT_TRANSITIONS, isActiveInvestigation, sourceDescription, TRANSITION_VERBS } from "../../lib/misc";
import { AlertsTable } from "./AlertsTable";
import { InvestigatePanel } from "./InvestigatePanel";
import { Timeline } from "./Timeline";

const incidentPath = (id: string) => `/api/v1/incidents/${seg(id)}`;

function Lifecycle({ incident }: { incident: Incident }) {
  const qc = useQueryClient();
  const update = useMutation({
    mutationFn: (body: IncidentUpdate) => api.patch<Incident>(incidentPath(incident.id), body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["incident", incident.id] });
      void qc.invalidateQueries({ queryKey: ["incidents"] });
      void qc.invalidateQueries({ queryKey: ["audit"] });
    },
  });
  const next = INCIDENT_TRANSITIONS[incident.status] ?? [];
  return (
    <div className="lifecycle">
      <div className="button-row" role="group" aria-label="Lifecycle transitions">
        {next.length === 0 ? (
          <span className="muted small">Closed incidents cannot change status.</span>
        ) : (
          next.map((s: IncidentStatus) => (
            <button
              key={s}
              type="button"
              className={`btn btn-small${s === "closed" ? " btn-danger" : ""}`}
              disabled={update.isPending}
              onClick={() => update.mutate({ status: s })}
            >
              {TRANSITION_VERBS[s]}
            </button>
          ))
        )}
      </div>
      {incident.status !== "closed" ? (
        <p className="muted small">Resolving or closing ends alert grouping for this incident.</p>
      ) : null}
      {update.isError ? <ErrorNotice error={update.error} title="The status was not changed" /> : null}
    </div>
  );
}

function EditIncident({ incident, onDone }: { incident: Incident; onDone: () => void }) {
  const qc = useQueryClient();
  const titleId = useId();
  const sevId = useId();
  const [title, setTitle] = useState(incident.title);
  const [severity, setSeverity] = useState<Severity>(incident.severity);
  const update = useMutation({
    mutationFn: (body: IncidentUpdate) => api.patch<Incident>(incidentPath(incident.id), body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["incident", incident.id] });
      void qc.invalidateQueries({ queryKey: ["incidents"] });
      onDone();
    },
  });
  function submit(e: FormEvent) {
    e.preventDefault();
    const body: IncidentUpdate = {};
    if (title.trim() && title.trim() !== incident.title) body.title = title.trim();
    if (severity !== incident.severity) body.severity = severity;
    if (Object.keys(body).length === 0) onDone();
    else update.mutate(body);
  }
  return (
    <form className="form-grid" onSubmit={submit} aria-label="Edit incident">
      <div className="field field-wide">
        <label htmlFor={titleId}>Title</label>
        <input id={titleId} required maxLength={300} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={sevId}>Severity</label>
        <select id={sevId} value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {capitalize(s)}
            </option>
          ))}
        </select>
      </div>
      <div className="form-actions field-wide">
        <button type="submit" className="btn btn-primary btn-small" disabled={update.isPending}>
          Save
        </button>
        <button type="button" className="btn btn-small" onClick={onDone}>
          Cancel
        </button>
      </div>
      {update.isError ? (
        <div className="field-wide">
          <ErrorNotice error={update.error} />
        </div>
      ) : null}
    </form>
  );
}

function InvestigationsTable({ investigations }: { investigations: Investigation[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <caption className="visually-hidden">Investigations of this incident</caption>
        <thead>
          <tr>
            <th scope="col">Requested</th>
            <th scope="col">Status</th>
            <th scope="col">Source</th>
            <th scope="col">Verdict</th>
            <th scope="col">Support</th>
            <th scope="col">Finished</th>
            <th scope="col">
              <span className="visually-hidden">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {investigations.map((inv) => (
            <tr key={inv.id}>
              <td>
                <DateTime value={inv.created_at} />
                <div className="muted small">
                  {inv.trigger}
                  {inv.requested_by ? ` · ${inv.requested_by}` : ""}
                </div>
              </td>
              <td>
                <InvestigationStatusBadge status={inv.status} />
              </td>
              <td>
                {sourceDescription(inv.source)} {inv.synthetic ? <SyntheticBadge /> : null}
              </td>
              <td>{inv.classification ? <VerdictBadge classification={inv.classification} /> : <span className="muted">—</span>}</td>
              <td>{inv.support ? <SupportBadge support={inv.support} /> : <span className="muted">—</span>}</td>
              <td>
                <DateTime value={inv.finished_at} />
              </td>
              <td>
                <Link to={`/investigations/${encodeURIComponent(inv.id)}`}>Open</Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function IncidentDetailPage() {
  const { incidentId = "" } = useParams();
  const can = useCan();
  const [investigating, setInvestigating] = useState(false);
  const [editing, setEditing] = useState(false);

  const incidentQ = useQuery({
    queryKey: qk.incident(incidentId),
    queryFn: () => api.get<IncidentDetail>(incidentPath(incidentId)),
  });
  const alertsQ = useInfiniteQuery({
    queryKey: qk.incidentAlerts(incidentId),
    queryFn: ({ pageParam, signal }) =>
      api.get<Page<Alert>>(`${incidentPath(incidentId)}/alerts`, { limit: 100, cursor: pageParam }, { signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: incidentQ.isSuccess,
  });
  const investigationsQ = useQuery({
    queryKey: qk.incidentInvestigations(incidentId),
    queryFn: () => api.get<Investigation[]>(`${incidentPath(incidentId)}/investigations`),
    enabled: incidentQ.isSuccess && can(P.investigationRead),
    refetchInterval: (q) => (q.state.data?.some((i) => isActiveInvestigation(i.status)) ? 5_000 : false),
  });
  const auditQ = useQuery({
    queryKey: qk.audit({ target_type: "incident", target_id: incidentId }),
    queryFn: () =>
      api.get<Page<AuditEvent>>("/api/v1/audit", { target_type: "incident", target_id: incidentId, limit: 50 }),
    enabled: incidentQ.isSuccess && can(P.auditRead),
  });

  if (incidentQ.isPending) return <Loading label="Loading incident…" />;
  if (incidentQ.isError) {
    return (
      <div className="page">
        <PageHeader title="Incident" />
        <ErrorNotice
          error={incidentQ.error}
          title={isNotFound(incidentQ.error) ? "Incident not found" : "The incident could not be loaded"}
          onRetry={() => void incidentQ.refetch()}
        />
      </div>
    );
  }
  const incident = incidentQ.data;
  const alerts = alertsQ.data?.pages.flatMap((p) => p.items) ?? [];
  const investigations = investigationsQ.data ?? [];
  const active = investigations.find((i) => isActiveInvestigation(i.status));

  return (
    <div className="page">
      <PageHeader
        kicker={<Link to="/incidents">Incidents</Link>}
        title={incident.title}
        actions={
          <>
            {can(P.investigationStart) && !investigating ? (
              <button type="button" className="btn btn-primary" onClick={() => setInvestigating(true)}>
                Investigate
              </button>
            ) : null}
            {can(P.incidentWrite) && !editing ? (
              <button type="button" className="btn" onClick={() => setEditing(true)}>
                Edit
              </button>
            ) : null}
          </>
        }
      >
        <SeverityBadge severity={incident.severity} />
        <IncidentStatusBadge status={incident.status} />
        {incident.grouping_open ? (
          <span className="badge neutral" title="New matching alerts are grouped into this incident">
            grouping open
          </span>
        ) : null}
      </PageHeader>

      {editing ? (
        <Panel title="Edit incident">
          <EditIncident incident={incident} onDone={() => setEditing(false)} />
        </Panel>
      ) : null}
      {investigating ? <InvestigatePanel incidentId={incident.id} onClose={() => setInvestigating(false)} /> : null}
      {active ? (
        <p className="notice notice-info">
          An investigation is {active.status}:{" "}
          <Link to={`/investigations/${encodeURIComponent(active.id)}`}>follow its progress</Link>.
        </p>
      ) : null}

      <div className="grid-2">
        <Panel title="Details">
          <dl className="kv">
            <div className="kv-row">
              <dt>Service</dt>
              <dd>{incident.service ?? "—"}</dd>
            </div>
            <div className="kv-row">
              <dt>Environment</dt>
              <dd>{incident.environment ?? "—"}</dd>
            </div>
            <div className="kv-row">
              <dt>Source</dt>
              <dd>{incident.source}</dd>
            </div>
            <div className="kv-row">
              <dt>Started</dt>
              <dd>
                <DateTime value={incident.started_at} relative />
              </dd>
            </div>
            <div className="kv-row">
              <dt>Resolved</dt>
              <dd>
                <DateTime value={incident.resolved_at} />
              </dd>
            </div>
            <div className="kv-row">
              <dt>Alerts</dt>
              <dd>
                {incident.alert_count}
                {incident.last_alert_at ? (
                  <span className="muted">
                    {" "}
                    · last <DateTime value={incident.last_alert_at} />
                  </span>
                ) : null}
              </dd>
            </div>
            <div className="kv-row">
              <dt>Investigations</dt>
              <dd>{incident.investigations}</dd>
            </div>
            <div className="kv-row">
              <dt>Created</dt>
              <dd>
                <DateTime value={incident.created_at} />
                {incident.created_by ? <span className="muted"> by {incident.created_by}</span> : null}
              </dd>
            </div>
          </dl>
          {incident.description ? (
            <>
              <h3 className="subhead">Description</h3>
              <p className="prewrap">{incident.description}</p>
            </>
          ) : null}
        </Panel>
        <Panel title="Status">
          {can(P.incidentWrite) ? <Lifecycle incident={incident} /> : <p className="muted small">Your role cannot change the status.</p>}
          <h3 className="subhead">Labels</h3>
          <KeyValueList data={incident.labels} className="kv-compact" />
          <h3 className="subhead">Annotations</h3>
          <KeyValueList data={incident.annotations} className="kv-compact" />
        </Panel>
      </div>

      <Panel title="Investigations">
        {!can(P.investigationRead) ? (
          <p className="muted">Your role cannot read investigations.</p>
        ) : investigationsQ.isPending ? (
          <Loading />
        ) : investigationsQ.isError ? (
          <ErrorNotice error={investigationsQ.error} onRetry={() => void investigationsQ.refetch()} />
        ) : investigations.length === 0 ? (
          <EmptyState title="No investigation yet">
            {can(P.investigationStart) ? <p>Use “Investigate” to start one.</p> : null}
          </EmptyState>
        ) : (
          <InvestigationsTable investigations={investigations} />
        )}
      </Panel>

      <Panel title={`Alerts (${incident.alert_count})`}>
        {alertsQ.isPending ? (
          <Loading />
        ) : alertsQ.isError ? (
          <ErrorNotice error={alertsQ.error} onRetry={() => void alertsQ.refetch()} />
        ) : alerts.length === 0 ? (
          <EmptyState title="No alerts">
            <p>This incident was created manually or its alerts were not retained.</p>
          </EmptyState>
        ) : (
          <>
            <AlertsTable alerts={alerts} />
            <LoadMore
              hasNextPage={alertsQ.hasNextPage}
              isFetchingNextPage={alertsQ.isFetchingNextPage}
              onLoadMore={() => void alertsQ.fetchNextPage()}
              shown={alerts.length}
              noun="alerts"
            />
          </>
        )}
      </Panel>

      <Panel title="Timeline">
        <Timeline incident={incident} alerts={alerts} investigations={investigations} />
      </Panel>

      {can(P.auditRead) ? (
        <Panel title="Audit trail">
          {auditQ.isPending ? (
            <Loading />
          ) : auditQ.isError ? (
            <ErrorNotice error={auditQ.error} />
          ) : auditQ.data.items.length === 0 ? (
            <p className="muted">No audit events for this incident.</p>
          ) : (
            <>
              <AuditTable events={auditQ.data.items} caption="Audit events for this incident" />
              {auditQ.data.next_cursor ? (
                <p className="small">
                  Showing the latest 50.{" "}
                  <Link to={`/settings/audit?target_type=incident&target_id=${encodeURIComponent(incident.id)}`}>
                    Open the full audit log
                  </Link>
                  .
                </p>
              ) : null}
            </>
          )}
        </Panel>
      ) : null}
    </div>
  );
}
