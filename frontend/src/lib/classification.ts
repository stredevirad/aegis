// Verdict vocabulary (docs/rca.md §4-§5, production-architecture §5). The display wording is
// fixed by the architecture: never "root cause found" for anything weaker than PLAUSIBLE,
// never "no anomaly" for a declared incident.

export type VerdictTone = "strong" | "likely" | "insufficient" | "nothing" | "unknown";

export interface VerdictPresentation {
  classification: string | null;
  label: string;
  tone: VerdictTone;
  /** Only CONFIRMED and PLAUSIBLE may present root causes as findings. */
  presentsFindings: boolean;
  explanation: string;
}

const PRESENTATIONS: Record<string, Omit<VerdictPresentation, "classification">> = {
  CONFIRMED: {
    label: "Strongly supported",
    tone: "strong",
    presentsFindings: true,
    explanation:
      "Independent modalities agree, the failure propagates from this service, the baseline is valid and no check failed.",
  },
  PLAUSIBLE: {
    label: "Likely cause",
    tone: "likely",
    presentsFindings: true,
    explanation:
      "At least one substantive check passed and none failed, but the evidence does not meet the bar for 'strongly supported'.",
  },
  INSUFFICIENT: {
    label: "Not enough evidence",
    tone: "insufficient",
    presentsFindings: false,
    explanation:
      "Anomalies were found but the evidence does not support naming a root cause. Follow the next checks below.",
  },
  NO_ANOMALY: {
    label: "Nothing found in what was checked",
    tone: "nothing",
    presentsFindings: false,
    explanation:
      "No anomaly, error origin, new error signature or changed call pattern was found in the telemetry that was collected. This does not mean nothing is wrong; see what was not checked.",
  },
};

export function presentClassification(classification: string | null | undefined): VerdictPresentation {
  const key = classification ?? "";
  const p = PRESENTATIONS[key];
  if (p) return { classification: key, ...p };
  return {
    classification: classification ?? null,
    label: "No verdict",
    tone: "unknown",
    presentsFindings: false,
    explanation: "This investigation has no verdict.",
  };
}

export function classificationLabel(classification: string | null | undefined): string {
  return presentClassification(classification).label;
}

const SUPPORT_LABELS: Record<string, string> = {
  strong: "Strong",
  moderate: "Moderate",
  weak: "Weak",
  none: "None",
};

/** Support is ordinal (strong > moderate > weak > none), never a probability. */
export function supportLabel(support: string | null | undefined): string {
  if (!support) return "—";
  return SUPPORT_LABELS[support] ?? support;
}

export type CheckTone = "pass" | "fail" | "not_evaluable" | "not_applicable" | "unknown";

const CHECK_STATE_LABELS: Record<string, string> = {
  pass: "Pass",
  fail: "Fail",
  not_evaluable: "Not evaluable",
  not_applicable: "Not applicable",
};

export function checkStateLabel(state: string): string {
  return CHECK_STATE_LABELS[state] ?? state;
}

export function checkTone(state: string): CheckTone {
  return state in CHECK_STATE_LABELS ? (state as CheckTone) : "unknown";
}

const CHECK_NAMES: Record<string, string> = {
  baseline_valid: "Baseline valid",
  temporal_precedence: "Temporal precedence",
  propagation: "Propagation",
  isolation: "Isolation",
  downstream_evaluated: "Downstream evaluated",
  change_scope: "Change scope",
  modality_agreement: "Modality agreement",
};

export function checkName(name: string): string {
  return CHECK_NAMES[name] ?? name.replace(/_/g, " ");
}

const MECHANISMS: Record<string, string> = {
  "change_induced:deployment": "Change-induced (deployment)",
  "change_induced:config": "Change-induced (configuration)",
  "change_induced:feature_flag": "Change-induced (feature flag)",
  "change_induced:rollout": "Change-induced (rollout)",
  "change_induced:other": "Change-induced (other change)",
  saturation: "Saturation",
  dependency_latency: "Dependency latency",
  dependency_failure: "Dependency failure",
  error_throwing: "Errors thrown by the service",
  latency_degradation: "Latency degradation",
  traffic_surge: "Traffic surge",
  behaviour_change: "Behaviour change (call pattern)",
  unknown: "Unknown mechanism",
};

export function mechanismLabel(mechanism: string | null | undefined): string {
  if (!mechanism) return "—";
  return MECHANISMS[mechanism] ?? mechanism.replace(/_/g, " ");
}

const HYPOTHESES: Record<string, string> = {
  internal_failure: "Internal failure",
  dependency_failure: "Dependency failure",
  change_induced: "Change-induced",
  saturation: "Saturation",
  traffic_surge: "Traffic surge",
  behaviour_change: "Behaviour change",
};

export function hypothesisLabel(kind: string): string {
  return HYPOTHESES[kind] ?? kind.replace(/_/g, " ");
}

const AI_STATUS: Record<string, string> = {
  disabled: "AI adjudication is disabled for this organization",
  accepted: "Accepted (validated against the deterministic result)",
  invalid: "Rejected: the model output failed validation; the deterministic verdict stands",
  unavailable: "Unavailable: the deterministic verdict stands",
  refused: "Refused by policy: the deterministic verdict stands",
  budget_exhausted: "Not run: the daily token budget is exhausted",
  cancelled: "Cancelled",
};

export function aiStatusLabel(status: string): string {
  return AI_STATUS[status] ?? status;
}

const COLLECTION_LABELS: Record<string, string> = {
  ok: "Collected",
  partial: "Partial",
  failed: "Failed",
  not_configured: "Not configured",
};

export function collectionStatusLabel(status: string): string {
  return COLLECTION_LABELS[status] ?? status;
}

export function isCollectionProblem(status: string): boolean {
  return status !== "ok";
}
