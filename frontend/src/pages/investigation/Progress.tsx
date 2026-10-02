import { useMemo } from "react";
import type { InvestigationEvent } from "../../api/types";
import { CollectionBadge } from "../../components/Badge";
import { DateTime } from "../../components/Time";
import { formatTime } from "../../lib/format";
import { deriveProgress, type Step, type StepStatus } from "../../lib/steps";
import { useInvestigationStream, type StreamMode } from "../../hooks/useInvestigationStream";

const STATUS_TEXT: Record<StepStatus, string> = {
  done: "done",
  active: "in progress",
  pending: "pending",
  skipped: "skipped",
  not_reached: "not reached",
  failed: "failed",
  cancelled: "cancelled",
};

const MODE_TEXT: Record<StreamMode, string> = {
  connecting: "Connecting to the live stream…",
  live: "Live",
  reconnecting: "Reconnecting to the live stream…",
  polling: "Polling for progress",
  history: "Loading history…",
  ended: "Stream ended",
};

function StepRow({ step }: { step: Step }) {
  return (
    <li className={`step step-${step.status}`}>
      <span className="step-marker" aria-hidden="true" />
      <div className="step-main">
        <div className="step-line">
          <span className="step-label">{step.label}</span>
          <span className="visually-hidden">: </span>
          <span className="step-status">{STATUS_TEXT[step.status]}</span>
          {step.ts ? <span className="step-ts">{formatTime(step.ts)}</span> : null}
        </div>
        {step.message && step.key !== "collected" ? <div className="step-message">{step.message}</div> : null}
        {step.collected?.length ? (
          <ul className="step-collected">
            {step.collected.map((c) => (
              <li key={c.seq}>
                <span className="step-modality">{c.modality}</span>
                <span className="muted small">{c.provider}</span>
                <CollectionBadge status={c.status} />
                {c.items !== null ? <span className="muted small">{c.items} items</span> : null}
                {c.truncated ? <span className="badge warn">truncated</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </li>
  );
}

export function ProgressView({
  events,
  mode,
  error,
}: {
  events: InvestigationEvent[];
  mode: StreamMode;
  error: string | null;
}) {
  const progress = useMemo(() => deriveProgress(events), [events]);
  const last = events[events.length - 1];
  return (
    <div className="progress">
      <p className={`stream-mode stream-${mode}`}>
        <span className="stream-dot" aria-hidden="true" />
        {MODE_TEXT[mode]}
        {progress.attempt ? <span className="muted"> · attempt {progress.attempt}</span> : null}
      </p>
      {error ? <p className="small warn-text">{error}</p> : null}
      {progress.retryScheduled ? (
        <p className="notice notice-warning small">{progress.retryScheduled.message}</p>
      ) : null}
      {progress.cancelRequested && !progress.terminal ? (
        <p className="notice notice-warning small">
          Cancellation requested: the worker stops at the next stage boundary.
        </p>
      ) : null}
      <ol className="steps" aria-label="Investigation steps">
        {progress.steps.map((s) => (
          <StepRow key={s.key} step={s} />
        ))}
      </ol>
      {/* Screen readers hear each new step as it arrives. */}
      <p className="visually-hidden" aria-live="polite" aria-atomic="true">
        {last ? `${last.kind.replace(/_/g, " ")}: ${last.message}` : ""}
      </p>
      <details className="disclosure">
        <summary>Event log ({events.length})</summary>
        <ol className="event-log">
          {events.map((e) => (
            <li key={e.seq}>
              <span className="event-seq">#{e.seq}</span> <DateTime value={e.ts} />{" "}
              <span className="badge neutral">{e.kind}</span> <span>{e.message}</span>
              {e.actor ? <span className="muted small"> ({e.actor})</span> : null}
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

/** Live progress panel; mount with key={investigationId}. */
export function LiveProgress({
  investigationId,
  live,
  onEvent,
  onTerminal,
}: {
  investigationId: string;
  live: boolean;
  onEvent?: (e: InvestigationEvent) => void;
  onTerminal?: (kind: string) => void;
}) {
  const stream = useInvestigationStream(investigationId, { live, onEvent, onTerminal });
  return <ProgressView events={stream.events} mode={stream.mode} error={stream.error} />;
}
