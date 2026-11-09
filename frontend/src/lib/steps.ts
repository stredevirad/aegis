import type { InvestigationEvent } from "../api/types";

// Investigation progress derived from the persisted event history (ADR-010):
// queued → claimed → collecting → collected (per modality) → snapshot stored → analyzing →
// hypotheses → diagnosis → [AI assessment] → completed | failed | cancelled.
//
// Only the latest attempt counts: a retry starts again at "claimed", and an attempt that reuses
// an earlier snapshot emits "snapshot_stored" without "collecting"/"collected" (shown as skipped).

export const EVENT_KINDS = [
  "queued",
  "deferred",
  "claimed",
  "collecting",
  "collected",
  "snapshot_stored",
  "analyzing",
  "hypotheses",
  "diagnosis",
  "ai_adjudication",
  "completed",
  "retry_scheduled",
  "failed",
  "cancel_requested",
  "cancelled",
] as const;

export const TERMINAL_EVENT_KINDS: ReadonlySet<string> = new Set(["completed", "failed", "cancelled"]);

export type StepStatus =
  | "done"
  | "active"
  | "pending"
  | "skipped"
  | "not_reached"
  | "failed"
  | "cancelled";

export interface CollectedItem {
  seq: number;
  modality: string;
  provider: string;
  status: string;
  items: number | null;
  truncated: boolean;
}

export interface Step {
  key: string;
  label: string;
  status: StepStatus;
  ts?: string;
  message?: string;
  collected?: CollectedItem[];
}

export interface Progress {
  steps: Step[];
  terminal: "completed" | "failed" | "cancelled" | null;
  attempt: number | null;
  retryScheduled: InvestigationEvent | null;
  cancelRequested: boolean;
  deferred: InvestigationEvent | null;
}

const LABELS: Record<string, { label: string; active: string }> = {
  queued: { label: "Queued", active: "Queued" },
  claimed: { label: "Claimed by a worker", active: "Waiting for a worker" },
  collecting: { label: "Collecting telemetry", active: "Collecting telemetry" },
  collected: { label: "Collected per modality", active: "Waiting for collection results" },
  snapshot_stored: { label: "Snapshot stored", active: "Storing the snapshot" },
  analyzing: { label: "Analyzing", active: "Analyzing" },
  hypotheses: { label: "Hypotheses ranked", active: "Ranking hypotheses" },
  diagnosis: { label: "Diagnosis", active: "Computing the diagnosis" },
  ai_adjudication: { label: "AI assessment", active: "AI assessment" },
};

function str(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value ? value : fallback;
}

function toCollected(e: InvestigationEvent): CollectedItem {
  const d = e.data ?? {};
  return {
    seq: e.seq,
    modality: str(d.modality),
    provider: str(d.provider),
    status: str(d.status, "unknown"),
    items: typeof d.items === "number" ? d.items : null,
    truncated: d.truncated === true,
  };
}

function findLastIndex<T>(list: T[], pred: (x: T) => boolean): number {
  for (let i = list.length - 1; i >= 0; i--) if (pred(list[i] as T)) return i;
  return -1;
}

export function deriveProgress(events: InvestigationEvent[]): Progress {
  const sorted = [...events].sort((a, b) => a.seq - b.seq);
  const claimIdx = findLastIndex(sorted, (e) => e.kind === "claimed");
  const attempt = claimIdx >= 0 ? sorted.slice(claimIdx) : [];
  const first = (kind: string, list: InvestigationEvent[]) => list.find((e) => e.kind === kind);

  const terminalEvent = [...sorted].reverse().find((e) => TERMINAL_EVENT_KINDS.has(e.kind)) ?? null;
  const terminal = (terminalEvent?.kind ?? null) as Progress["terminal"];
  const lastEvent = sorted[sorted.length - 1] ?? null;
  const retryScheduled =
    !terminal && lastEvent && lastEvent.kind === "retry_scheduled" ? lastEvent : null;
  const cancelRequested = sorted.some((e) => e.kind === "cancel_requested");
  const deferred = [...sorted].reverse().find((e) => e.kind === "deferred") ?? null;

  const claimed = claimIdx >= 0 ? sorted[claimIdx] : undefined;
  const queued = first("queued", sorted) ?? first("deferred", sorted);
  const collecting = first("collecting", attempt);
  const snapshot = first("snapshot_stored", attempt);
  const collected = attempt.filter((e) => e.kind === "collected");
  const reused = Boolean(snapshot && !collecting);

  const found: Record<string, InvestigationEvent | undefined> = {
    queued: queued ?? (claimed ? claimed : undefined),
    claimed,
    collecting,
    collected: collected[0],
    snapshot_stored: snapshot,
    analyzing: first("analyzing", attempt),
    hypotheses: first("hypotheses", attempt),
    diagnosis: first("diagnosis", attempt),
    ai_adjudication: first("ai_adjudication", attempt),
  };

  const keys = Object.keys(LABELS).filter((k) => k !== "ai_adjudication" || found.ai_adjudication);
  const steps: Step[] = keys.map((key) => {
    const ev = found[key];
    const labels = LABELS[key] ?? { label: key, active: key };
    const step: Step = { key, label: labels.label, status: ev ? "done" : "pending" };
    if (ev) {
      step.ts = ev.ts;
      step.message = key === "queued" && ev.kind === "claimed" ? undefined : ev.message;
    }
    if (key === "collected" && collected.length) step.collected = collected.map(toCollected);
    if ((key === "collecting" || key === "collected") && reused) {
      step.status = "skipped";
      step.message = "Reused the snapshot stored by an earlier attempt";
    }
    if (key === "queued" && deferred) {
      step.message = deferred.message;
    }
    return step;
  });

  const lastDone = findLastIndex(steps, (s) => s.status === "done");
  steps.forEach((s, i) => {
    if (s.status === "pending" && i < lastDone) s.status = "skipped";
  });

  if (terminal) {
    for (const s of steps) if (s.status === "pending") s.status = "not_reached";
  } else if (!retryScheduled) {
    const next = steps.find((s) => s.status === "pending");
    if (next) {
      next.status = "active";
      next.label = LABELS[next.key]?.active ?? next.label;
    }
  }

  const outcome: Step = { key: "outcome", label: "Outcome", status: "pending" };
  if (terminalEvent) {
    outcome.ts = terminalEvent.ts;
    outcome.message = terminalEvent.message;
    outcome.label =
      terminal === "completed" ? "Completed" : terminal === "failed" ? "Failed" : "Cancelled";
    outcome.status = terminal === "completed" ? "done" : terminal === "failed" ? "failed" : "cancelled";
  }
  steps.push(outcome);

  const attemptNo = claimed && typeof claimed.data?.attempt === "number" ? claimed.data.attempt : null;
  return { steps, terminal, attempt: attemptNo, retryScheduled, cancelRequested, deferred };
}
