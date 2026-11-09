import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchAllEvents, qk } from "../../api/queries";
import type { Alert, Incident, Investigation } from "../../api/types";
import { InvestigationStatusBadge, SyntheticBadge, VerdictBadge } from "../../components/Badge";
import { ErrorNotice, Loading } from "../../components/Feedback";
import { DateTime } from "../../components/Time";
import { formatTime, parseDate } from "../../lib/format";
import { sourceDescription } from "../../lib/misc";

interface Entry {
  ts: string;
  key: string;
  kind: "incident" | "alert" | "investigation";
  content: ReactNode;
}

function InvestigationEvents({ investigationId }: { investigationId: string }) {
  const q = useQuery({
    queryKey: qk.events(investigationId),
    queryFn: ({ signal }) => fetchAllEvents(investigationId, signal),
  });
  if (q.isPending) return <Loading label="Loading events…" />;
  if (q.isError) return <ErrorNotice error={q.error} />;
  if (!q.data.length) return <p className="muted small">No events.</p>;
  return (
    <ol className="event-log">
      {q.data.map((e) => (
        <li key={e.seq}>
          <span className="event-seq">{formatTime(e.ts)}</span> <span className="badge neutral">{e.kind}</span>{" "}
          <span>{e.message}</span>
        </li>
      ))}
    </ol>
  );
}

function EventsDisclosure({ investigationId }: { investigationId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="disclosure disclosure-inline" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Events</summary>
      {open ? <InvestigationEvents investigationId={investigationId} /> : null}
    </details>
  );
}

/** Incident, alert and investigation activity in time order (all from the API). */
export function Timeline({
  incident,
  alerts,
  investigations,
}: {
  incident: Incident;
  alerts: Alert[];
  investigations: Investigation[];
}) {
  const entries: Entry[] = [
    { ts: incident.started_at, key: "started", kind: "incident", content: "Incident started" },
    {
      ts: incident.created_at,
      key: "created",
      kind: "incident",
      content: `Incident created (source: ${incident.source}${incident.created_by ? `, by ${incident.created_by}` : ""})`,
    },
  ];
  if (incident.resolved_at) {
    entries.push({ ts: incident.resolved_at, key: "resolved", kind: "incident", content: "Incident resolved" });
  }
  for (const a of alerts) {
    entries.push({
      ts: a.first_received_at,
      key: `alert-${a.id}`,
      kind: "alert",
      content: (
        <>
          Alert <strong>{a.name}</strong> received ({a.status}) — {a.receive_count} receipt
          {a.receive_count === 1 ? "" : "s"}, last at <DateTime value={a.last_received_at} />
        </>
      ),
    });
    if (a.ends_at) {
      entries.push({
        ts: a.ends_at,
        key: `alert-end-${a.id}`,
        kind: "alert",
        content: (
          <>
            Alert <strong>{a.name}</strong> ended
          </>
        ),
      });
    }
  }
  for (const inv of investigations) {
    entries.push({
      ts: inv.created_at,
      key: `inv-${inv.id}`,
      kind: "investigation",
      content: (
        <>
          <Link to={`/investigations/${encodeURIComponent(inv.id)}`}>Investigation</Link> requested ({inv.trigger}
          {inv.requested_by ? `, by ${inv.requested_by}` : ""}; {sourceDescription(inv.source)}){" "}
          {inv.synthetic ? <SyntheticBadge compact /> : null} <InvestigationStatusBadge status={inv.status} />
          <EventsDisclosure investigationId={inv.id} />
        </>
      ),
    });
    if (inv.finished_at) {
      entries.push({
        ts: inv.finished_at,
        key: `inv-end-${inv.id}`,
        kind: "investigation",
        content: (
          <>
            <Link to={`/investigations/${encodeURIComponent(inv.id)}`}>Investigation</Link> {inv.status}
            {inv.classification ? (
              <>
                {" "}
                — <VerdictBadge classification={inv.classification} />
              </>
            ) : null}
            {inv.synthetic ? (
              <>
                {" "}
                <SyntheticBadge compact />
              </>
            ) : null}
          </>
        ),
      });
    }
  }
  entries.sort((a, b) => (parseDate(a.ts)?.getTime() ?? 0) - (parseDate(b.ts)?.getTime() ?? 0));
  return (
    <ol className="timeline">
      {entries.map((e) => (
        <li key={e.key} className={`timeline-item timeline-${e.kind}`}>
          <span className="timeline-ts">
            <DateTime value={e.ts} />
          </span>
          <span className="timeline-content">{e.content}</span>
        </li>
      ))}
    </ol>
  );
}
