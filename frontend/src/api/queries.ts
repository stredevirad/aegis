import { QueryClient } from "@tanstack/react-query";
import { api, isApiError, seg, type QueryParams } from "./client";
import type {
  Evidence,
  Graph,
  Investigation,
  InvestigationEvent,
  Page,
  SnapshotSummary,
} from "./types";

export const qk = {
  me: ["me"] as const,
  authConfig: ["auth-config"] as const,
  incidents: (params: QueryParams) => ["incidents", params] as const,
  incident: (id: string) => ["incident", id] as const,
  incidentAlerts: (id: string) => ["incident", id, "alerts"] as const,
  incidentInvestigations: (id: string) => ["incident", id, "investigations"] as const,
  investigations: (params: QueryParams) => ["investigations", params] as const,
  investigation: (id: string) => ["investigation", id] as const,
  events: (id: string) => ["investigation", id, "events"] as const,
  evidencePage: (id: string, params: QueryParams) => ["investigation", id, "evidence", params] as const,
  evidenceAll: (id: string) => ["investigation", id, "evidence-all"] as const,
  evidenceItem: (id: string, evidenceId: string) => ["investigation", id, "evidence-item", evidenceId] as const,
  graph: (id: string) => ["investigation", id, "graph"] as const,
  snapshot: (id: string) => ["investigation", id, "snapshot"] as const,
  sources: ["sources"] as const,
  changes: (params: QueryParams) => ["changes", params] as const,
  organization: ["organization"] as const,
  members: ["members"] as const,
  invitations: ["invitations"] as const,
  apiKeys: ["api-keys"] as const,
  connectors: ["connectors"] as const,
  integrations: ["integrations"] as const,
  audit: (params: QueryParams) => ["audit", params] as const,
};

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          // Client errors are answers, not transient failures.
          if (isApiError(error) && error.status >= 400 && error.status < 500 && error.status !== 429) {
            return false;
          }
          return failureCount < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}

export const investigationPath = (id: string) => `/api/v1/investigations/${seg(id)}`;

export function fetchInvestigation(id: string) {
  return api.get<Investigation>(investigationPath(id));
}

export function fetchGraph(id: string) {
  return api.get<Graph>(`${investigationPath(id)}/graph`);
}

export function fetchSnapshot(id: string) {
  return api.get<SnapshotSummary>(`${investigationPath(id)}/snapshot`);
}

export function fetchEvents(id: string, after: number, limit = 500, signal?: AbortSignal) {
  return api.get<InvestigationEvent[]>(`${investigationPath(id)}/events`, { after, limit }, { signal });
}

/** Every event of an investigation (the REST endpoint pages by sequence number). */
export async function fetchAllEvents(id: string, signal?: AbortSignal): Promise<InvestigationEvent[]> {
  const out: InvestigationEvent[] = [];
  let after = 0;
  for (let page = 0; page < 20; page++) {
    const batch = await fetchEvents(id, after, 500, signal);
    out.push(...batch);
    const last = batch[batch.length - 1];
    if (batch.length < 500 || !last) break;
    after = last.seq;
  }
  return out;
}

export const EVIDENCE_PAGE_LIMIT = 200;
export const EVIDENCE_MAX_PAGES = 10;

/** All evidence of an investigation, up to a cap (the RCA view resolves citations against it). */
export async function fetchAllEvidence(
  id: string,
  signal?: AbortSignal,
): Promise<{ items: Evidence[]; truncated: boolean }> {
  const items: Evidence[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < EVIDENCE_MAX_PAGES; page++) {
    const res: Page<Evidence> = await api.get<Page<Evidence>>(
      `${investigationPath(id)}/evidence`,
      { limit: EVIDENCE_PAGE_LIMIT, cursor },
      { signal },
    );
    items.push(...res.items);
    cursor = res.next_cursor;
    if (!cursor) return { items, truncated: false };
  }
  return { items, truncated: true };
}

export function fetchEvidenceItem(id: string, evidenceId: string) {
  return api.get<Evidence>(`${investigationPath(id)}/evidence/${seg(evidenceId)}`);
}
