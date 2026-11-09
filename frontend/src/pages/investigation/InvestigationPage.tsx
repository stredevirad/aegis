import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, isNotFound, seg } from "../../api/client";
import { fetchInvestigation, fetchSnapshot, investigationPath, qk } from "../../api/queries";
import type { CancelOut, Incident, Investigation, InvestigationEvent } from "../../api/types";
import { useCan } from "../../auth/context";
import { P } from "../../auth/permissions";
import { InvestigationStatusBadge, SyntheticBadge, VerdictBadge } from "../../components/Badge";
import { ErrorNotice, Loading, Notice } from "../../components/Feedback";
import { PageHeader, Panel } from "../../components/Layout";
import { DateTime, WindowRange } from "../../components/Time";
import { durationBetween, humanize } from "../../lib/format";
import { isActiveInvestigation, randomToken, sourceDescription, TERMINAL_INVESTIGATION } from "../../lib/misc";
import { CollectionSummary } from "./CollectionSummary";
import { LiveProgress } from "./Progress";
import { RcaView } from "./RcaView";

const REFRESH_ON = new Set([
  "claimed",
  "retry_scheduled",
  "cancel_requested",
  "diagnosis",
  "ai_adjudication",
]);

function Overview({ inv }: { inv: Investigation }) {
  return (
    <dl className="kv">
      <div className="kv-row">
        <dt>Status</dt>
        <dd>
          <InvestigationStatusBadge status={inv.status} />
          {inv.cancel_requested && isActiveInvestigation(inv.status) ? (
            <span className="badge warn">cancel requested</span>
          ) : null}
        </dd>
      </div>
      <div className="kv-row">
        <dt>Queue state</dt>
        <dd>
          {inv.queue_state ? humanize(inv.queue_state) : "—"}
          {inv.attempts !== null ? <span className="muted"> · attempts: {inv.attempts}</span> : null}
        </dd>
      </div>
      <div className="kv-row">
        <dt>Source</dt>
        <dd>
          {sourceDescription(inv.source)} {inv.synthetic ? <SyntheticBadge /> : null}
        </dd>
      </div>
      <div className="kv-row">
        <dt>Incident window</dt>
        <dd>
          <WindowRange start={inv.incident_window.start} end={inv.incident_window.end} />
          <span className="muted small"> ({durationBetween(inv.incident_window.start, inv.incident_window.end)})</span>
        </dd>
      </div>
      <div className="kv-row">
        <dt>Baseline window</dt>
        <dd>
          <WindowRange start={inv.baseline_window.start} end={inv.baseline_window.end} />
          <span className="muted small"> ({durationBetween(inv.baseline_window.start, inv.baseline_window.end)})</span>
        </dd>
      </div>
      <div className="kv-row">
        <dt>Trigger</dt>
        <dd>
          {inv.trigger}
          {inv.requested_by ? <span className="muted"> · requested by {inv.requested_by}</span> : null}
        </dd>
      </div>
      <div className="kv-row">
        <dt>Created</dt>
        <dd>
          <DateTime value={inv.created_at} relative />
        </dd>
      </div>
      <div className="kv-row">
        <dt>Started</dt>
        <dd>
          <DateTime value={inv.started_at} />
        </dd>
      </div>
      <div className="kv-row">
        <dt>Finished</dt>
        <dd>
          <DateTime value={inv.finished_at} />
          {inv.started_at && inv.finished_at ? (
            <span className="muted small"> (ran {durationBetween(inv.started_at, inv.finished_at)})</span>
          ) : null}
        </dd>
      </div>
      {inv.classification ? (
        <div className="kv-row">
          <dt>Verdict</dt>
          <dd>
            <VerdictBadge classification={inv.classification} />
          </dd>
        </div>
      ) : null}
    </dl>
  );
}

export function InvestigationPage() {
  const { investigationId = "" } = useParams();
  const qc = useQueryClient();
  const can = useCan();
  const navigate = useNavigate();
  const [retryKey] = useState(randomToken);
  const [cancelResult, setCancelResult] = useState<string | null>(null);

  const invQ = useQuery({
    queryKey: qk.investigation(investigationId),
    queryFn: () => fetchInvestigation(investigationId),
    // A slow safety net next to the stream (queue state and attempts are not in events).
    refetchInterval: (q) => (q.state.data && isActiveInvestigation(q.state.data.status) ? 15_000 : false),
  });
  const inv = invQ.data;
  // The snapshot exists only once the worker has stored it: asking earlier returns 404, which the
  // browser reports as a failed request. A completed investigation always has one; otherwise wait
  // for the "snapshot_stored" event (the event history is replayed when the page loads).
  const [snapshotStoredFor, setSnapshotStoredFor] = useState<string | null>(null);
  const snapshotExpected = inv?.status === "completed" || snapshotStoredFor === investigationId;
  const snapQ = useQuery({
    queryKey: qk.snapshot(investigationId),
    queryFn: () => fetchSnapshot(investigationId),
    enabled: Boolean(inv) && can(P.evidenceRead) && snapshotExpected,
  });
  const incidentQ = useQuery({
    queryKey: qk.incident(inv?.incident_id ?? ""),
    queryFn: () => api.get<Incident>(`/api/v1/incidents/${seg(inv?.incident_id ?? "")}`),
    enabled: Boolean(inv),
  });

  const cancel = useMutation({
    mutationFn: () => api.post<CancelOut>(`${investigationPath(investigationId)}/cancel`),
    onSuccess: (res) => {
      setCancelResult(
        res.result === "cancelled"
          ? "The investigation was cancelled."
          : "Cancellation requested: the worker stops at the next stage boundary.",
      );
      void qc.invalidateQueries({ queryKey: qk.investigation(investigationId) });
    },
  });
  const retry = useMutation({
    mutationFn: () =>
      api.post<Investigation>(`${investigationPath(investigationId)}/retry`, undefined, {
        headers: { "Idempotency-Key": retryKey },
      }),
    onSuccess: (created) => {
      void qc.invalidateQueries({ queryKey: ["investigations"] });
      void qc.invalidateQueries({ queryKey: qk.incidentInvestigations(created.incident_id) });
      navigate(`/investigations/${encodeURIComponent(created.id)}`);
    },
  });

  if (invQ.isPending) return <Loading label="Loading investigation…" />;
  if (invQ.isError) {
    return (
      <>
        <PageHeader title="Investigation" />
        <ErrorNotice
          error={invQ.error}
          title={isNotFound(invQ.error) ? "Investigation not found" : "The investigation could not be loaded"}
          onRetry={() => void invQ.refetch()}
        />
      </>
    );
  }
  const investigation = invQ.data;
  const live = !TERMINAL_INVESTIGATION.has(investigation.status);

  const onEvent = (e: InvestigationEvent) => {
    if (REFRESH_ON.has(e.kind)) void qc.invalidateQueries({ queryKey: qk.investigation(investigationId), exact: true });
    if (e.kind === "snapshot_stored") {
      setSnapshotStoredFor(investigationId);
      void qc.invalidateQueries({ queryKey: qk.snapshot(investigationId) });
    }
  };
  const onTerminal = () => {
    void qc.invalidateQueries({ queryKey: ["investigation", investigationId] });
    void qc.invalidateQueries({ queryKey: ["incident", investigation.incident_id] });
    void qc.invalidateQueries({ queryKey: ["investigations"] });
  };

  const canCancel =
    can(P.investigationCancel) && isActiveInvestigation(investigation.status) && !investigation.cancel_requested;
  const canRetry = can(P.investigationStart) && TERMINAL_INVESTIGATION.has(investigation.status);
  const hasVerdict = Boolean(investigation.classification);
  const incidentTitle = incidentQ.data?.title;

  return (
    <div className="page investigation-page">
      <PageHeader
        kicker={
          <Link to={`/incidents/${encodeURIComponent(investigation.incident_id)}`}>
            Incident{incidentTitle ? `: ${incidentTitle}` : ""}
          </Link>
        }
        title={
          <>
            Investigation <span className="muted mono small">{investigation.id}</span>
          </>
        }
        actions={
          <>
            {canCancel ? (
              <button type="button" className="btn btn-danger" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                {cancel.isPending ? "Cancelling…" : "Cancel investigation"}
              </button>
            ) : null}
            {canRetry ? (
              <button type="button" className="btn" onClick={() => retry.mutate()} disabled={retry.isPending}>
                {retry.isPending ? "Starting…" : "Retry (same source and windows)"}
              </button>
            ) : null}
            {hasVerdict && can(P.evidenceRead) ? (
              <Link className="btn" to={`/investigations/${encodeURIComponent(investigation.id)}/evidence`}>
                Evidence explorer
              </Link>
            ) : null}
          </>
        }
      >
        <InvestigationStatusBadge status={investigation.status} />
        {investigation.synthetic ? <SyntheticBadge /> : null}
      </PageHeader>

      {investigation.synthetic ? (
        <Notice tone="warning" title="SYNTHETIC DATA" role="note">
          <p>
            This investigation analysed a synthetic benchmark scenario ({sourceDescription(investigation.source)}),
            not telemetry from your systems. Its findings say nothing about your production services.
          </p>
        </Notice>
      ) : null}
      {cancel.isError ? <ErrorNotice error={cancel.error} title="Cancel failed" /> : null}
      {retry.isError ? <ErrorNotice error={retry.error} title="Retry failed" /> : null}
      {cancelResult ? (
        <Notice tone="info" role="status">
          <p>{cancelResult}</p>
        </Notice>
      ) : null}
      {investigation.status === "failed" && investigation.error ? (
        <Notice tone="error" title="The investigation failed" role="alert">
          <p>{investigation.error}</p>
        </Notice>
      ) : null}

      <div className="grid-2">
        <Panel title="Overview">
          <Overview inv={investigation} />
        </Panel>
        <Panel title="Progress">
          <LiveProgress
            key={investigation.id}
            investigationId={investigation.id}
            live={live}
            onEvent={onEvent}
            onTerminal={onTerminal}
          />
        </Panel>
      </div>

      {can(P.evidenceRead) ? (
        <Panel title="Collection summary">
          {!snapshotExpected ? (
            <p className="muted">
              {live ? "No telemetry snapshot yet: collection has not finished." : "No telemetry snapshot was stored."}
            </p>
          ) : snapQ.isPending ? (
            <Loading label="Loading collection summary…" />
          ) : snapQ.isError ? (
            isNotFound(snapQ.error) ? (
              <p className="muted">
                {live ? "No telemetry snapshot yet: collection has not finished." : "No telemetry snapshot was stored."}
              </p>
            ) : (
              <ErrorNotice error={snapQ.error} onRetry={() => void snapQ.refetch()} />
            )
          ) : (
            <CollectionSummary snapshot={snapQ.data} />
          )}
        </Panel>
      ) : null}

      {hasVerdict && can(P.evidenceRead) ? (
        <>
          {live ? (
            <Notice tone="info" role="status">
              <p>The deterministic verdict is available; the investigation is still finishing (for example the AI assessment).</p>
            </Notice>
          ) : null}
          <RcaView investigation={investigation} snapshot={snapQ.data} />
        </>
      ) : hasVerdict ? (
        <Notice tone="info">
          <p>Your role cannot read evidence, so the analysis details are not shown.</p>
        </Notice>
      ) : null}
    </div>
  );
}
