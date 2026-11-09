import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Alert, Evidence, Investigation } from "../api/types";
import { AlertsTable } from "../pages/incident/AlertsTable";
import { AiAssessmentLayer, VerdictSummary } from "../pages/investigation/RcaView";
import { EvidenceItem } from "./Evidence";
import { SafeExternalLink } from "./ExternalLink";
import { KeyValueList } from "./KeyValues";

// Telemetry is hostile (security model §2, C19): everything must come out as literal text.
const SCRIPT = "<script>alert(1)</script>";
const IMG = "<img src=x onerror=alert(1)>";
const JS_LINK = "javascript:alert(1)";
const MARKDOWN = "[x](javascript:alert(1))";
const HTML_LINK = '<a href="javascript:alert(1)">click</a>';

function assertInert(container: HTMLElement) {
  expect(container.querySelectorAll("script")).toHaveLength(0);
  expect(container.querySelectorAll("img")).toHaveLength(0);
  expect(container.querySelectorAll("iframe, object, embed")).toHaveLength(0);
  for (const a of Array.from(container.querySelectorAll("a"))) {
    const href = (a.getAttribute("href") ?? "").trim().toLowerCase();
    expect(href.startsWith("javascript:")).toBe(false);
    expect(href.startsWith("data:")).toBe(false);
  }
  for (const el of Array.from(container.querySelectorAll("*"))) {
    for (const attr of Array.from(el.attributes)) {
      expect(attr.name.startsWith("on")).toBe(false);
    }
  }
  expect(container.querySelectorAll("[style]")).toHaveLength(0);
}

const hostileEvidence: Evidence = {
  evidence_id: "ev-1",
  kind: "log_signature",
  modality: "logs",
  service: IMG,
  observed_at: "2026-10-01T10:00:00Z",
  summary: `${SCRIPT} new error signature ${MARKDOWN}`,
  attributes: { template: HTML_LINK, link: JS_LINK, nested: { html: SCRIPT } },
  sources: [{ provider: IMG, query: `{service="${SCRIPT}"}` }],
};

describe("hostile strings render as text", () => {
  it("evidence summary, service, attributes and provenance", () => {
    const { container } = render(
      <EvidenceItem evidence={hostileEvidence} expanded onToggle={() => {}} />,
    );
    assertInert(container);
    expect(screen.getByText(IMG, { selector: ".evidence-service" })).toBeInTheDocument();
    expect(screen.getByText(`${SCRIPT} new error signature ${MARKDOWN}`)).toBeInTheDocument();
    expect(screen.getByText(HTML_LINK)).toBeInTheDocument();
    expect(screen.getByText(JS_LINK)).toBeInTheDocument();
    expect(screen.getByText(`{service="${SCRIPT}"}`)).toBeInTheDocument();
  });

  it("alert labels, annotations, name and generator URL", () => {
    const alert: Alert = {
      id: "a1",
      incident_id: "i1",
      source: "alertmanager",
      fingerprint: "fp",
      status: "firing",
      name: SCRIPT,
      severity: "critical",
      service: IMG,
      environment: MARKDOWN,
      labels: { alertname: SCRIPT, service: IMG },
      annotations: { summary: MARKDOWN, description: HTML_LINK, runbook: JS_LINK },
      generator_url: JS_LINK,
      starts_at: "2026-10-01T10:00:00Z",
      ends_at: null,
      first_received_at: "2026-10-01T10:00:00Z",
      last_received_at: "2026-10-01T10:05:00Z",
      receive_count: 3,
    };
    const { container } = render(<AlertsTable alerts={[alert]} />);
    assertInert(container);
    expect(container.querySelectorAll("a")).toHaveLength(0); // the javascript: URL is not linked
    expect(screen.getAllByText(SCRIPT).length).toBeGreaterThan(0);
    expect(screen.getByText(HTML_LINK)).toBeInTheDocument();
    expect(screen.getByText(JS_LINK)).toBeInTheDocument();
    expect(screen.getByText("(link withheld)")).toBeInTheDocument();
  });

  it("annotations rendered through KeyValueList", () => {
    const { container } = render(<KeyValueList data={{ [SCRIPT]: IMG, md: MARKDOWN }} />);
    assertInert(container);
    expect(screen.getByText(SCRIPT)).toBeInTheDocument();
    expect(screen.getByText(IMG)).toBeInTheDocument();
    expect(screen.getByText(MARKDOWN)).toBeInTheDocument();
  });

  it("only safe generator URLs become links, with noopener noreferrer", () => {
    const { container } = render(
      <>
        <SafeExternalLink href="https://prom.example/graph">ok</SafeExternalLink>
        <SafeExternalLink href={JS_LINK}>bad</SafeExternalLink>
        <SafeExternalLink href="data:text/html,<script>alert(1)</script>">bad2</SafeExternalLink>
      </>,
    );
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0]?.getAttribute("href")).toBe("https://prom.example/graph");
    expect(links[0]?.getAttribute("rel")).toBe("noopener noreferrer");
    expect(links[0]?.getAttribute("target")).toBe("_blank");
    assertInert(container);
  });

  it("verdict text and AI claims", () => {
    const inv = {
      id: "inv-1",
      classification: "PLAUSIBLE",
      support: "weak",
      display_label: SCRIPT,
      summary: `${IMG} ${MARKDOWN}`,
      blast_radius: [HTML_LINK],
      ai: null,
    } as unknown as Investigation;
    const { container } = render(
      <MemoryRouter>
        <VerdictSummary investigation={inv} />
        <AiAssessmentLayer
          ai={{
            status: "accepted",
            classification: "PLAUSIBLE",
            support: "weak",
            summary: SCRIPT,
            claims: [{ type: "observation", text: MARKDOWN, evidence_ids: [IMG] }],
            uncertainty: HTML_LINK,
            missing_information: [JS_LINK],
          }}
          onSelect={() => {}}
        />
      </MemoryRouter>,
    );
    assertInert(container);
    expect(screen.getAllByText(SCRIPT).length).toBe(2);
    expect(screen.getByText(MARKDOWN)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Show evidence ${IMG}` })).toBeInTheDocument();
  });
});
