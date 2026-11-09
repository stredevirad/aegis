import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Investigation, InvestigationEvent, InvestigationStatus, SnapshotSummary } from "../../api/types";
import { jsonResponse, mockFetch, renderWithProviders } from "../../test/utils";
import { InvestigationPage } from "./InvestigationPage";

function investigation(status: InvestigationStatus): Investigation {
  return {
    id: "inv-1",
    incident_id: "inc-1",
    status,
    queue_state: status === "running" ? "claimed" : null,
    attempts: 1,
    trigger: "manual",
    requested_by: "user-1",
    source: { kind: "scenario", scenario: "s06-feature-flag-failure" },
    synthetic: true,
    incident_window: { start: "2026-10-01T10:00:00Z", end: "2026-10-01T10:15:00Z" },
    baseline_window: { start: "2026-10-01T09:00:00Z", end: "2026-10-01T09:45:00Z" },
    classification: null,
    support: null,
    display_label: null,
    summary: null,
    root_causes: [],
    reasons: [],
    next_checks: [],
    coverage_gaps: [],
    blast_radius: [],
    other_anomalies: [],
    error: null,
    cancel_requested: false,
    created_at: "2026-10-01T10:16:00Z",
    started_at: "2026-10-01T10:16:01Z",
    finished_at: status === "completed" ? "2026-10-01T10:16:02Z" : null,
  };
}

function event(seq: number, kind: string): InvestigationEvent {
  return { seq, ts: "2026-10-01T10:16:01Z", kind, state: null, actor: "worker", message: kind, data: {} };
}

const snapshot: SnapshotSummary = {
  investigation_id: "inv-1",
  digest: "sha256:abc",
  synthetic: true,
  schema_version: "1",
  captured_at: "2026-10-01T10:16:01Z",
  collected_window: null,
  counts: { metrics: 3 },
  collection: [{ modality: "metrics", provider: "scenario", status: "ok", detail: "", items: 3, truncated: false }],
  redaction: {},
};

function setup(status: InvestigationStatus, events: InvestigationEvent[]) {
  const fetchMock = mockFetch([
    ["GET", /^\/api\/v1\/investigations\/inv-1$/, () => jsonResponse(investigation(status))],
    ["GET", /^\/api\/v1\/investigations\/inv-1\/events$/, (url) =>
      jsonResponse(events.filter((e) => e.seq > Number(url.searchParams.get("after") ?? 0)))],
    ["GET", /^\/api\/v1\/investigations\/inv-1\/snapshot$/, () => jsonResponse(snapshot)],
  ]);
  renderWithProviders(<InvestigationPage />, {
    route: "/investigations/inv-1",
    path: "/investigations/:investigationId",
  });
  const snapshotRequested = () =>
    fetchMock.mock.calls.some(([input]) => String(input).includes("/investigations/inv-1/snapshot"));
  const eventsRequested = () =>
    fetchMock.mock.calls.some(([input]) => String(input).includes("/investigations/inv-1/events"));
  return { snapshotRequested, eventsRequested };
}

describe("InvestigationPage snapshot request", () => {
  it("does not ask for the snapshot before the worker has stored it", async () => {
    const { snapshotRequested, eventsRequested } = setup("running", [event(1, "queued"), event(2, "claimed")]);
    expect(await screen.findByText("No telemetry snapshot yet: collection has not finished.")).toBeInTheDocument();
    await waitFor(() => expect(eventsRequested()).toBe(true));
    expect(snapshotRequested()).toBe(false);
  });

  it("asks for the snapshot once the snapshot_stored event arrives", async () => {
    const { snapshotRequested } = setup("running", [event(1, "claimed"), event(2, "snapshot_stored")]);
    await waitFor(() => expect(snapshotRequested()).toBe(true));
  });

  it("asks for the snapshot of a completed investigation right away", async () => {
    const { snapshotRequested } = setup("completed", []);
    await waitFor(() => expect(snapshotRequested()).toBe(true));
  });
});
