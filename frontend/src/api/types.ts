// Response and request shapes of /api/v1 and /auth (backend/aegis/api/schemas.py and routers).
// Every string in these objects is untrusted data: render it as React text only.

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export type IncidentStatus = "open" | "investigating" | "resolved" | "closed";
export type InvestigationStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type Classification = "CONFIRMED" | "PLAUSIBLE" | "INSUFFICIENT" | "NO_ANOMALY";
export type SupportLevel = "strong" | "moderate" | "weak" | "none";
export type CheckState = "pass" | "fail" | "not_evaluable" | "not_applicable";
export type CollectionStatus = "ok" | "partial" | "failed" | "not_configured";
export type Role = "admin" | "investigator" | "reviewer" | "viewer";

export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];
export const INCIDENT_STATUSES: IncidentStatus[] = ["open", "investigating", "resolved", "closed"];
export const ROLES: Role[] = ["admin", "investigator", "reviewer", "viewer"];
export const EVIDENCE_KINDS = [
  "signal_anomaly",
  "signal_normal",
  "error_origin",
  "trace_exemplar",
  "log_signature",
  "change",
  "edge_change",
  "coverage_gap",
] as const;
export const MODALITIES = ["metrics", "logs", "traces", "changes"] as const;
export const CHANGE_KINDS = [
  "deployment",
  "config",
  "feature_flag",
  "rollout",
  "infrastructure",
  "other",
] as const;
export const API_KEY_SCOPES = ["ingest:alerts", "ingest:changes", "read"] as const;
export type ApiKeyScope = (typeof API_KEY_SCOPES)[number];

export type JsonObject = Record<string, unknown>;

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

// --- identity ---------------------------------------------------------------------------------

export interface OrgRef {
  id: string;
  slug: string;
  name: string;
  role: string;
}

export interface Me {
  kind: "user" | "api_key";
  org: OrgRef | null;
  user_id: string | null;
  email: string | null;
  display_name: string | null;
  role: string | null;
  permissions: string[];
  memberships: OrgRef[];
  api_key_prefix: string | null;
}

export interface AuthConfig {
  mode: string;
  dev_login: boolean;
}

export interface SessionOut {
  authenticated: boolean;
  org_id?: string | null;
  role?: string | null;
  expires_at?: string | null;
}

// --- incidents --------------------------------------------------------------------------------

export interface Incident {
  id: string;
  title: string;
  description: string;
  severity: Severity;
  status: IncidentStatus;
  service: string | null;
  environment: string | null;
  source: string;
  labels: JsonObject;
  annotations: JsonObject;
  started_at: string;
  resolved_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  last_alert_at: string | null;
  alert_count: number;
  grouping_open: boolean;
  auto_investigated_at: string | null;
}

export interface IncidentDetail extends Incident {
  investigations: number;
  latest_investigation: Investigation | null;
}

export interface IncidentCreate {
  title: string;
  severity: Severity;
  description?: string;
  service?: string;
  environment?: string;
  started_at?: string;
}

export interface IncidentUpdate {
  title?: string;
  severity?: Severity;
  status?: IncidentStatus;
}

export interface Alert {
  id: string;
  incident_id: string;
  source: string;
  fingerprint: string;
  status: string;
  name: string;
  severity: Severity;
  service: string | null;
  environment: string | null;
  labels: JsonObject;
  annotations: JsonObject;
  generator_url: string | null;
  starts_at: string;
  ends_at: string | null;
  first_received_at: string;
  last_received_at: string;
  receive_count: number;
}

// --- investigations ---------------------------------------------------------------------------

export interface TimeWindow {
  start: string;
  end: string;
}

export interface RootCause {
  service: string;
  mechanism: string;
  support: string;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
}

export interface NextCheck {
  action: string;
  reason: string;
  services: string[];
}

export type SourceSpec = { kind: "live" } | { kind: "scenario"; scenario: string };

export type AiStatus =
  | "disabled"
  | "accepted"
  | "invalid"
  | "unavailable"
  | "refused"
  | "budget_exhausted"
  | "cancelled";

export interface AiClaim {
  type: "observation" | "inference" | string;
  text: string;
  evidence_ids: string[];
}

/** The AI assessment attached to a completed investigation's verdict (ADR-009). It can only
 * lower the deterministic result; it never replaces it. Optional: older APIs do not send it. */
export interface AiAssessment {
  status: AiStatus | string;
  reason?: string | null;
  classification?: string | null;
  support?: string | null;
  selected_candidate?: string | null;
  mechanism?: string | null;
  claims?: AiClaim[];
  uncertainty?: string | null;
  missing_information?: string[];
  summary?: string | null;
  next_check?: string | null;
  contradicting_evidence_ids?: string[];
  uncited_claims_dropped?: number | null;
  lowered?: boolean;
  disagreement?: boolean;
  provider?: string | null;
  model?: string | null;
  locality?: string | null;
  /** Validation errors when the output was rejected (status "invalid"). */
  errors?: string[];
}

export interface Investigation {
  id: string;
  incident_id: string;
  status: InvestigationStatus;
  queue_state: string | null;
  attempts: number | null;
  trigger: string;
  requested_by: string | null;
  source: JsonObject;
  synthetic: boolean;
  incident_window: TimeWindow;
  baseline_window: TimeWindow;
  classification: string | null;
  support: string | null;
  display_label: string | null;
  summary: string | null;
  root_causes: RootCause[];
  reasons: string[];
  next_checks: NextCheck[];
  coverage_gaps: string[];
  blast_radius: string[];
  other_anomalies: string[];
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  ai?: AiAssessment | null;
}

export interface InvestigationStart {
  source?: SourceSpec;
  window?: TimeWindow;
}

export interface CancelOut {
  investigation_id: string;
  result: "cancelled" | "cancel_requested";
}

export interface InvestigationEvent {
  seq: number;
  ts: string;
  kind: string;
  state: string | null;
  actor: string | null;
  message: string;
  data: JsonObject;
}

export interface Evidence {
  evidence_id: string;
  kind: string;
  modality: string | null;
  service: string | null;
  observed_at: string | null;
  summary: string;
  attributes: JsonObject;
  sources: JsonObject[];
}

export interface Check {
  name: string;
  state: CheckState | string;
  reason: string;
  evidence_ids: string[];
  qualifier?: string | null;
}

export interface Candidate {
  service: string;
  rank: number;
  rule: string | null;
  rule_sentence: string | null;
  hypothesis: string;
  inferred: boolean;
  features: JsonObject;
  checks: Check[];
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
}

export interface Relation {
  source: string;
  target: string;
  kind: string;
}

export interface Graph {
  investigation_id: string;
  candidates: Candidate[];
  root_causes: RootCause[];
  relations: Relation[];
  decision_trace: string[];
}

export interface CollectionOutcome {
  modality: string;
  provider: string;
  status: CollectionStatus | string;
  detail: string;
  items: number;
  truncated: boolean;
}

export interface SnapshotSummary {
  investigation_id: string;
  digest: string;
  synthetic: boolean;
  schema_version: string;
  captured_at: string | null;
  collected_window: TimeWindow | null;
  counts: Record<string, number>;
  collection: CollectionOutcome[];
  redaction: JsonObject;
}

export interface Sources {
  live: boolean;
  scenarios: string[];
  note: string;
}

// --- change events ----------------------------------------------------------------------------

export interface ChangeEvent {
  id: string;
  source: string;
  external_id: string | null;
  kind: string;
  service: string | null;
  environment: string | null;
  version: string | null;
  summary: string;
  actor: string | null;
  ts: string;
  received_at: string;
  scope: JsonObject;
  metadata: JsonObject;
}

// --- administration ---------------------------------------------------------------------------

export interface AuditEvent {
  seq: number;
  ts: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: JsonObject;
  request_id: string | null;
  hash: string;
}

export interface AuditVerify {
  intact: boolean;
  first_broken_seq: number | null;
  events: number;
  head_hash: string | null;
  note: string;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  created_by: string | null;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  rotated_from: string | null;
  active: boolean;
}

export interface ApiKeyCreated {
  key: ApiKey;
  secret: string;
}

export interface Member {
  user_id: string;
  email: string | null;
  display_name: string | null;
  role: string;
  created_at: string;
}

export interface Invitation {
  id: string;
  email: string;
  role: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

export interface OrgSettings {
  auto_investigate: boolean;
  default_source: SourceSpec | null;
  grouping_window_minutes: number;
  auto_resolve: boolean;
  remediation_enabled: boolean;
  // AI adjudication (API migration 0006+). Optional: older APIs do not return them.
  ai_enabled?: boolean;
  ai_integration_id?: string | null;
  ai_model?: string | null;
  ai_allow_cloud?: boolean;
  ai_daily_token_budget?: number;
  ai_max_output_tokens?: number;
}

export interface Organization {
  id: string;
  slug: string;
  name: string;
  settings: OrgSettings;
}

export interface Connector {
  id: string;
  kind: string;
  scheme: string;
  host: string;
  port: number;
}

export type CredentialType = "none" | "bearer" | "basic" | "header";

export interface CredentialIn {
  type: CredentialType;
  token?: string;
  username?: string;
  password?: string;
  header_name?: string;
  header_value?: string;
}

export interface Integration {
  id: string;
  connector_id: string;
  kind: string;
  name: string;
  enabled: boolean;
  credential_type: string;
  has_credential: boolean;
  connector_available: boolean;
  created_at: string;
  updated_at: string;
  last_test_at: string | null;
  last_test_status: string | null;
  last_test_detail: string | null;
}

export interface IntegrationTest {
  status: "ok" | "error";
  category: string | null;
  detail: string;
}
