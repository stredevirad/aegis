import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { SEVERITIES, type Incident, type Integration, type Investigation, type Page } from "../api/types";
import { useCan } from "../auth/context";
import { P } from "../auth/permissions";
import { InvestigationStatusBadge, SeverityBadge, SyntheticBadge, VerdictBadge } from "../components/Badge";
import { EmptyState, ErrorNotice, Loading } from "../components/Feedback";
import { PageHeader, Panel } from "../components/Layout";
import { Relative } from "../components/Time";
import { capitalize, shortHash } from "../lib/format";
import { sourceDescription } from "../lib/misc";
import { IncidentTable } from "./IncidentsPage";

const OPEN_STATUSES = ["open", "investigating"];
const COUNT_LIMIT = 200;
const REFRESH_MS = 30_000;

function countLabel(page: Page<unknown> | undefined): string {
  if (!page) return "—";
  return page.next_cursor ? `${page.items.length}+` : String(page.items.length);
}

function OpenBySeverity() {
  const results = useQueries({
    queries: SEVERITIES.map((severity) => ({
      queryKey: ["dashboard", "open-by-severity", severity],
      queryFn: () =>
        api.get<Page<Incident>>("/api/v1/incidents", {
          status: OPEN_STATUSES,
          severity: [severity],
          limit: COUNT_LIMIT,
        }),
      refetchInterval: REFRESH_MS,
    })),
  });
  const error = results.find((r) => r.isError)?.error;
  if (error) return <ErrorNotice error={error} />;
  if (results.some((r) => r.isPending)) return <Loading />;
  const counts = results.map((r) => r.data?.items.length ?? 0);
  const max = Math.max(1, ...counts);
  const total = counts.reduce((a, b) => a + b, 0);
  const anyCapped = results.some((r) => r.data?.next_cursor);
  return (
    <>
      <p className="big-number">
        {total}
        {anyCapped ? "+" : ""} <span className="muted small">open or investigating</span>
      </p>
      <table className="table table-compact severity-table">
        <caption className="visually-hidden">Open incidents by severity</caption>
        <thead>
          <tr>
            <th scope="col">Severity</th>
            <th scope="col" className="num">
              Open
            </th>
            <th scope="col">
              <span className="visually-hidden">Share</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {SEVERITIES.map((sev, i) => (
            <tr key={sev}>
              <td>
                <Link to={`/incidents?status=open&status=investigating&severity=${sev}`}>
                  <SeverityBadge severity={sev} />
                </Link>
              </td>
              <td className="num">{countLabel(results[i]?.data)}</td>
              <td className="bar-cell">
                <svg className={`bar bar-${sev}`} viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true" focusable="false">
                  <rect x="0" y="0" width="100" height="8" className="bar-track" />
                  <rect x="0" y="0" width={((counts[i] ?? 0) / max) * 100} height="8" className="bar-fill" />
                </svg>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function InvestigationColumn({ status, title }: { status: string; title: string }) {
  const q = useQuery({
    queryKey: ["dashboard", "investigations", status],
    queryFn: () => api.get<Page<Investigation>>("/api/v1/investigations", { status: [status], limit: 10 }),
    refetchInterval: status === "failed" ? REFRESH_MS : 10_000,
  });
  return (
    <section className="inv-column" aria-label={title}>
      <h3 className="subhead">
        {title} <span className="count">{q.data ? countLabel(q.data) : ""}</span>
      </h3>
      {q.isPending ? (
        <Loading />
      ) : q.isError ? (
        <ErrorNotice error={q.error} />
      ) : q.data.items.length === 0 ? (
        <p className="muted small">None.</p>
      ) : (
        <ul className="inv-list">
          {q.data.items.map((inv) => (
            <li key={inv.id}>
              <Link to={`/investigations/${encodeURIComponent(inv.id)}`} className="mono">
                {shortHash(inv.id, 8)}
              </Link>{" "}
              <InvestigationStatusBadge status={inv.status} />
              {inv.synthetic ? <SyntheticBadge compact /> : null}
              {inv.classification ? <VerdictBadge classification={inv.classification} /> : null}
              <div className="muted small">
                {sourceDescription(inv.source)} · <Relative value={inv.created_at} />
                {inv.queue_state ? ` · queue: ${inv.queue_state}` : ""}
                {inv.attempts ? ` · attempts: ${inv.attempts}` : ""}
              </div>
              {inv.status === "failed" && inv.error ? <div className="small warn-text">{inv.error}</div> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function IntegrationsHealth() {
  const q = useQuery({
    queryKey: ["dashboard", "integrations"],
    queryFn: () => api.get<Integration[]>("/api/v1/integrations"),
    refetchInterval: REFRESH_MS,
  });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorNotice error={q.error} />;
  if (q.data.length === 0) {
    return (
      <EmptyState title="No integrations configured">
        <p>
          Live investigations need at least one telemetry integration. <Link to="/settings/integrations">Configure integrations</Link>.
        </p>
      </EmptyState>
    );
  }
  return (
    <div className="table-wrap">
      <table className="table table-compact">
        <caption className="visually-hidden">Integration health (last connection test)</caption>
        <thead>
          <tr>
            <th scope="col">Integration</th>
            <th scope="col">Kind</th>
            <th scope="col">Enabled</th>
            <th scope="col">Last test</th>
            <th scope="col">Detail</th>
          </tr>
        </thead>
        <tbody>
          {q.data.map((i) => (
            <tr key={i.id}>
              <td>{i.name}</td>
              <td>{i.kind}</td>
              <td>{i.enabled ? "Yes" : <span className="muted">No</span>}</td>
              <td>
                {i.last_test_status ? (
                  <span className={`badge ${i.last_test_status === "ok" ? "test-ok" : "test-error"}`}>
                    {capitalize(i.last_test_status)}
                  </span>
                ) : (
                  <span className="muted">never tested</span>
                )}{" "}
                {i.last_test_at ? <Relative value={i.last_test_at} /> : null}
                {!i.connector_available ? <span className="badge test-error">connector removed</span> : null}
              </td>
              <td className="small">{i.last_test_detail ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecentIncidents() {
  const q = useQuery({
    queryKey: ["dashboard", "recent-incidents"],
    queryFn: () => api.get<Page<Incident>>("/api/v1/incidents", { limit: 10, sort: "-created_at" }),
    refetchInterval: REFRESH_MS,
  });
  if (q.isPending) return <Loading />;
  if (q.isError) return <ErrorNotice error={q.error} />;
  if (q.data.items.length === 0) {
    return (
      <EmptyState title="No incidents yet">
        <p>Incidents appear here when alerts arrive or someone creates one.</p>
      </EmptyState>
    );
  }
  return <IncidentTable incidents={q.data.items} caption="Most recent incidents" />;
}

export function DashboardPage() {
  const can = useCan();
  return (
    <div className="page">
      <PageHeader title="Dashboard">
        <span className="muted small">
          Counts are taken from the incident and investigation lists and refresh automatically; “+” means
          more than were counted.
        </span>
      </PageHeader>
      <div className="grid-2">
        <Panel title="Open incidents by severity" actions={<Link to="/incidents?status=open&status=investigating">View all</Link>}>
          <OpenBySeverity />
        </Panel>
        {can(P.integrationRead) ? (
          <Panel title="Integrations health" actions={<Link to="/settings/integrations">Manage</Link>}>
            <IntegrationsHealth />
          </Panel>
        ) : null}
      </div>
      {can(P.investigationRead) ? (
        <Panel title="Investigations">
          <div className="grid-3">
            <InvestigationColumn status="running" title="Running" />
            <InvestigationColumn status="queued" title="Queued" />
            <InvestigationColumn status="failed" title="Recently failed" />
          </div>
        </Panel>
      ) : null}
      <Panel title="Recent incidents" actions={<Link to="/incidents">All incidents</Link>}>
        <RecentIncidents />
      </Panel>
    </div>
  );
}
