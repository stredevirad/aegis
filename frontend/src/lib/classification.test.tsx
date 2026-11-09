import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Graph, Investigation } from "../api/types";
import { VerdictBadge } from "../components/Badge";
import { AiAssessmentLayer, FindingsLayer } from "../pages/investigation/RcaView";
import { checkStateLabel, presentClassification, supportLabel } from "./classification";

function investigation(over: Partial<Investigation>): Investigation {
  return {
    id: "inv-1",
    incident_id: "inc-1",
    status: "completed",
    queue_state: "succeeded",
    attempts: 1,
    trigger: "manual",
    requested_by: "admin@example.com",
    source: { kind: "scenario", scenario: "s05-deployment-regression" },
    synthetic: true,
    incident_window: { start: "2026-10-01T10:00:00Z", end: "2026-10-01T10:30:00Z" },
    baseline_window: { start: "2026-10-01T09:00:00Z", end: "2026-10-01T10:00:00Z" },
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
    created_at: "2026-10-01T10:31:00Z",
    started_at: "2026-10-01T10:31:01Z",
    finished_at: "2026-10-01T10:31:05Z",
    ...over,
  };
}

const rootCause = {
  service: "checkout",
  mechanism: "change_induced:deployment",
  support: "strong",
  supporting_evidence_ids: ["ev-a"],
  contradicting_evidence_ids: [],
};

const graph: Graph = {
  investigation_id: "inv-1",
  candidates: [
    {
      service: "checkout",
      rank: 1,
      rule: "R2",
      rule_sentence: "An anomalous service with a recorded change shortly before its onset leads.",
      hypothesis: "change_induced",
      inferred: false,
      features: {},
      checks: [
        { name: "propagation", state: "pass", reason: "80% of failing traces end here", evidence_ids: ["ev-b"] },
        { name: "temporal_precedence", state: "not_evaluable", reason: "tie", evidence_ids: [], qualifier: "tie" },
      ],
      supporting_evidence_ids: ["ev-a"],
      contradicting_evidence_ids: [],
    },
  ],
  root_causes: [rootCause],
  relations: [],
  decision_trace: [],
};

describe("classification mapping", () => {
  it.each([
    ["CONFIRMED", "Strongly supported", "strong", true],
    ["PLAUSIBLE", "Likely cause", "likely", true],
    ["INSUFFICIENT", "Not enough evidence", "insufficient", false],
    ["NO_ANOMALY", "Nothing found in what was checked", "nothing", false],
  ])("%s → %s", (c, label, tone, findings) => {
    const p = presentClassification(c);
    expect(p.label).toBe(label);
    expect(p.tone).toBe(tone);
    expect(p.presentsFindings).toBe(findings);
  });

  it("unknown or missing classifications never present findings", () => {
    expect(presentClassification(null).presentsFindings).toBe(false);
    expect(presentClassification("SOMETHING_NEW").label).toBe("No verdict");
    expect(presentClassification("SOMETHING_NEW").presentsFindings).toBe(false);
  });

  it("badges use the display wording and never say 'no anomaly'", () => {
    const { container } = render(
      <>
        <VerdictBadge classification="CONFIRMED" />
        <VerdictBadge classification="NO_ANOMALY" />
      </>,
    );
    expect(container.querySelector(".verdict-strong")?.textContent).toBe("Strongly supported");
    expect(container.querySelector(".verdict-nothing")?.textContent).toBe("Nothing found in what was checked");
    expect(container.textContent?.toLowerCase()).not.toContain("no anomaly");
  });

  it("support and check states are ordinal labels", () => {
    expect(supportLabel("moderate")).toBe("Moderate");
    expect(supportLabel(null)).toBe("—");
    expect(checkStateLabel("not_evaluable")).toBe("Not evaluable");
    expect(checkStateLabel("not_applicable")).toBe("Not applicable");
  });
});

describe("verified finding layer", () => {
  it("CONFIRMED shows the root cause with its passed checks and clickable citations", async () => {
    const onSelect = vi.fn();
    render(
      <FindingsLayer
        investigation={investigation({ classification: "CONFIRMED", support: "strong", root_causes: [rootCause] })}
        graph={graph}
        graphState="ready"
        onSelect={onSelect}
      />,
    );
    const region = screen.getByRole("region", { name: /verified finding/i });
    expect(within(region).getByRole("heading", { name: "checkout" })).toBeInTheDocument();
    expect(within(region).getByText("Strongly supported")).toBeInTheDocument();
    expect(within(region).getByText("Propagation")).toBeInTheDocument();
    // a not-evaluable check is not listed among passed checks
    expect(within(region).queryByText("Temporal precedence")).not.toBeInTheDocument();
    await userEvent.click(within(region).getByRole("button", { name: "Show evidence ev-b" }));
    expect(onSelect).toHaveBeenCalledWith("ev-b");
  });

  it("INSUFFICIENT never renders a root cause as a finding, even if one is present", () => {
    render(
      <FindingsLayer
        investigation={investigation({
          classification: "INSUFFICIENT",
          support: "none",
          root_causes: [rootCause], // defensive: must not be shown
        })}
        graph={graph}
        graphState="ready"
        onSelect={() => {}}
      />,
    );
    // An abstention is never labelled a verified finding.
    expect(screen.queryByRole("region", { name: /verified finding/i })).not.toBeInTheDocument();
    const region = screen.getByRole("region", { name: /no finding/i });
    expect(within(region).queryByText("checkout")).not.toBeInTheDocument();
    expect(within(region).queryByRole("button")).not.toBeInTheDocument();
    expect(within(region).getByText(/no root cause is stated/i)).toBeInTheDocument();
  });

  it("NO_ANOMALY says nothing was found in what was checked", () => {
    render(
      <FindingsLayer
        investigation={investigation({ classification: "NO_ANOMALY", support: "none" })}
        graph={graph}
        graphState="ready"
        onSelect={() => {}}
      />,
    );
    expect(screen.queryByRole("region", { name: /verified finding/i })).not.toBeInTheDocument();
    const region = screen.getByRole("region", { name: /no finding/i });
    expect(within(region).getByText(/nothing was found in what was checked/i)).toBeInTheDocument();
  });
});

describe("AI assessment layer", () => {
  it("is not rendered when the API does not send an assessment", () => {
    const { container } = render(<AiAssessmentLayer ai={undefined} onSelect={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows lowered and disagreement notices and clickable claim citations", async () => {
    const onSelect = vi.fn();
    render(
      <AiAssessmentLayer
        ai={{
          status: "accepted",
          classification: "PLAUSIBLE",
          support: "weak",
          selected_candidate: "payments",
          claims: [
            { type: "observation", text: "Errors rose after the deploy", evidence_ids: ["ev-a"] },
            { type: "inference", text: "The deploy likely caused it", evidence_ids: ["ev-a", "ev-c"] },
          ],
          lowered: true,
          disagreement: true,
          provider: "local-ollama",
          model: "llama",
          locality: "local",
        }}
        onSelect={onSelect}
      />,
    );
    const region = screen.getByRole("region", { name: /ai assessment/i });
    expect(within(region).getByText("AI lowered the deterministic result")).toBeInTheDocument();
    expect(within(region).getByText(/disagrees with the deterministic root cause/i)).toBeInTheDocument();
    expect(within(region).getByText("Observation")).toBeInTheDocument();
    expect(within(region).getByText("Inference")).toBeInTheDocument();
    await userEvent.click(within(region).getByRole("button", { name: "Show evidence ev-c" }));
    expect(onSelect).toHaveBeenCalledWith("ev-c");
  });

  it("explains a non-accepted status without claims", () => {
    render(<AiAssessmentLayer ai={{ status: "invalid", errors: ["unknown evidence IDs cited: x"] }} onSelect={() => {}} />);
    const region = screen.getByRole("region", { name: /ai assessment/i });
    expect(within(region).getByText(/deterministic verdict stands/i)).toBeInTheDocument();
    expect(within(region).getByText("unknown evidence IDs cited: x")).toBeInTheDocument();
    expect(within(region).queryByText("Claims")).not.toBeInTheDocument();
  });
});
