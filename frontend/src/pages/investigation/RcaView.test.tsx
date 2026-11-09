import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Evidence, Graph, Investigation } from "../../api/types";
import { jsonResponse, mockFetch, renderWithProviders } from "../../test/utils";
import { RcaView } from "./RcaView";

function evidence(id: string, summary: string): Evidence {
  return {
    evidence_id: id,
    kind: "signal_anomaly",
    modality: "metrics",
    service: "catalog",
    observed_at: "2026-10-01T10:05:00Z",
    summary,
    attributes: { signal: "error_ratio" },
    sources: [{ provider: "prometheus", query: 'rate(http_errors{service="catalog"}[1m])' }],
  };
}

const inv: Investigation = {
  id: "inv-1",
  incident_id: "inc-1",
  status: "completed",
  queue_state: "succeeded",
  attempts: 1,
  trigger: "manual",
  requested_by: null,
  source: { kind: "live" },
  synthetic: false,
  incident_window: { start: "2026-10-01T10:00:00Z", end: "2026-10-01T10:20:00Z" },
  baseline_window: { start: "2026-10-01T09:00:00Z", end: "2026-10-01T10:00:00Z" },
  classification: "PLAUSIBLE",
  support: "moderate",
  display_label: "Likely cause: catalog",
  summary: "catalog error ratio rose",
  root_causes: [
    {
      service: "catalog",
      mechanism: "error_throwing",
      support: "moderate",
      supporting_evidence_ids: ["ev-a", "ev-z"],
      contradicting_evidence_ids: [],
    },
  ],
  reasons: ["propagation passed"],
  next_checks: [{ action: "Check the catalog database", reason: "dependency not instrumented", services: ["catalog"] }],
  coverage_gaps: ["traces: span status unknown for 30% of spans"],
  blast_radius: ["frontend"],
  other_anomalies: [],
  error: null,
  cancel_requested: false,
  created_at: "2026-10-01T10:21:00Z",
  started_at: "2026-10-01T10:21:01Z",
  finished_at: "2026-10-01T10:21:04Z",
};

const graph: Graph = {
  investigation_id: "inv-1",
  candidates: [
    {
      service: "catalog",
      rank: 1,
      rule: "R1",
      rule_sentence: "A service in which at least half of the failing traces end leads.",
      hypothesis: "internal_failure",
      inferred: false,
      features: { error_ratio: 0.12 },
      checks: [{ name: "propagation", state: "pass", reason: "70% of failing traces end here", evidence_ids: ["ev-b"] }],
      supporting_evidence_ids: ["ev-a"],
      contradicting_evidence_ids: [],
    },
  ],
  root_causes: inv.root_causes,
  relations: [],
  decision_trace: ["R1 selected catalog"],
};

describe("RcaView citations", () => {
  it("opens and focuses the cited evidence item, fetching one that is not in the list", async () => {
    const fetchMock = mockFetch([
      ["GET", /\/investigations\/inv-1\/graph$/, () => jsonResponse(graph)],
      [
        "GET",
        /\/investigations\/inv-1\/evidence$/,
        () => jsonResponse({ items: [evidence("ev-a", "catalog error ratio 12%"), evidence("ev-b", "failing traces end in catalog")], next_cursor: null }),
      ],
      ["GET", /\/investigations\/inv-1\/evidence\/ev-z$/, () => jsonResponse(evidence("ev-z", "deployment of catalog 2.3.1"))],
    ]);
    renderWithProviders(<RcaView investigation={inv} snapshot={null} />);

    const facts = await screen.findByRole("region", { name: /fact \/ observation/i });
    await within(facts).findByText("catalog error ratio 12%");
    const finding = screen.getByRole("region", { name: /verified finding/i });

    // a cited item from the list: expanded, highlighted and focused
    await userEvent.click(within(finding).getByRole("button", { name: "Show evidence ev-a" }));
    const item = within(facts).getByRole("article", { name: "Evidence ev-a" });
    await waitFor(() => expect(item).toHaveFocus());
    expect(within(item).getByRole("button", { expanded: true })).toBeInTheDocument();
    expect(within(item).getByText('rate(http_errors{service="catalog"}[1m])')).toBeInTheDocument();

    // a cited item outside the loaded list is fetched by ID
    await userEvent.click(within(finding).getByRole("button", { name: "Show evidence ev-z" }));
    const fetched = await within(facts).findByRole("article", { name: "Evidence ev-z" });
    await waitFor(() => expect(fetched).toHaveFocus());
    expect(within(fetched).getByText("deployment of catalog 2.3.1")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith("/evidence/ev-z"))).toBe(true);
  });

  it("keeps the layers apart: hypothesis, inference and uncertainty are labelled", async () => {
    mockFetch([
      ["GET", /\/graph$/, () => jsonResponse(graph)],
      ["GET", /\/evidence$/, () => jsonResponse({ items: [], next_cursor: null })],
    ]);
    renderWithProviders(<RcaView investigation={inv} snapshot={null} />);
    const hyp = await screen.findByRole("region", { name: /hypothesis/i });
    expect(within(hyp).getByText(/not conclusions/i)).toBeInTheDocument();
    const inference = screen.getByRole("region", { name: /inference/i });
    expect(within(inference).getByText("R1 selected catalog")).toBeInTheDocument();
    const unc = screen.getByRole("region", { name: /uncertainty/i });
    expect(within(unc).getByText("Check the catalog database")).toBeInTheDocument();
    expect(within(unc).getByText("traces: span status unknown for 30% of spans")).toBeInTheDocument();
    // no AI section when the API sends no assessment
    expect(screen.queryByRole("region", { name: /ai assessment/i })).not.toBeInTheDocument();
  });
});
