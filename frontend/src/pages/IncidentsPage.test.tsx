import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Incident, IncidentDetail } from "../api/types";
import { jsonResponse, makeMe, mockFetch, renderWithProviders } from "../test/utils";
import { IncidentDetailPage } from "./incident/IncidentDetailPage";
import { IncidentsPage } from "./IncidentsPage";

function incident(id: string, over: Partial<Incident> = {}): Incident {
  return {
    id,
    title: `Checkout errors ${id}`,
    description: "",
    severity: "critical",
    status: "open",
    service: "checkout",
    environment: "prod",
    source: "alertmanager",
    labels: {},
    annotations: {},
    started_at: "2026-10-01T10:00:00Z",
    resolved_at: null,
    created_by: null,
    created_at: "2026-10-01T10:00:05Z",
    updated_at: "2026-10-01T10:00:05Z",
    last_alert_at: "2026-10-01T10:05:00Z",
    alert_count: 2,
    grouping_open: true,
    auto_investigated_at: null,
    ...over,
  };
}

describe("IncidentsPage", () => {
  it("renders incident rows from the API and loads the next page", async () => {
    const fetchMock = mockFetch([
      [
        "GET",
        /^\/api\/v1\/incidents$/,
        (url) =>
          url.searchParams.get("cursor") === "c1"
            ? jsonResponse({ items: [incident("i3", { title: "Third", severity: "low" })], next_cursor: null })
            : jsonResponse({ items: [incident("i1"), incident("i2", { status: "investigating" })], next_cursor: "c1" }),
      ],
    ]);
    const { container } = renderWithProviders(<IncidentsPage />, { route: "/incidents" });
    const table = await screen.findByRole("table", { name: "Incidents" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3); // header + 2
    expect(within(table).getByRole("link", { name: "Checkout errors i1" })).toHaveAttribute("href", "/incidents/i1");
    expect(within(table).getByText("Investigating")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await within(table).findByRole("link", { name: "Third" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain("cursor=c1");
    expect(container.querySelectorAll("[style]")).toHaveLength(0);
  });

  it("shows the empty state when there are no incidents", async () => {
    mockFetch([["GET", /^\/api\/v1\/incidents$/, () => jsonResponse({ items: [], next_cursor: null })]]);
    renderWithProviders(<IncidentsPage />, { route: "/incidents" });
    expect(await screen.findByText("No incidents yet")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("sends the filters as query parameters", async () => {
    const fetchMock = mockFetch([["GET", /^\/api\/v1\/incidents$/, () => jsonResponse({ items: [], next_cursor: null })]]);
    renderWithProviders(<IncidentsPage />, { route: "/incidents" });
    await screen.findByText("No incidents yet");
    await userEvent.click(screen.getByRole("checkbox", { name: "Open" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Critical" }));
    await userEvent.type(screen.getByLabelText("Title contains"), "checkout");
    await userEvent.click(screen.getByRole("button", { name: "Apply filters" }));
    await screen.findByText("No incidents match these filters");
    const url = new URL(String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1]?.[0]), "http://x");
    expect(url.searchParams.getAll("status")).toEqual(["open"]);
    expect(url.searchParams.getAll("severity")).toEqual(["critical"]);
    expect(url.searchParams.get("q")).toBe("checkout");
    expect(url.searchParams.get("sort")).toBe("-created_at");
  });

  it("shows the API error message and request ID", async () => {
    mockFetch([
      [
        "GET",
        /^\/api\/v1\/incidents$/,
        () =>
          jsonResponse({ error: { code: "internal_error", message: "An internal error occurred", request_id: "req-42", details: [] } }, 500),
      ],
    ]);
    renderWithProviders(<IncidentsPage />, { route: "/incidents" });
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("An internal error occurred")).toBeInTheDocument();
    expect(within(alert).getByText("req-42")).toBeInTheDocument();
  });

  it("hides 'New incident' without incident.write", async () => {
    mockFetch([["GET", /^\/api\/v1\/incidents$/, () => jsonResponse({ items: [], next_cursor: null })]]);
    renderWithProviders(<IncidentsPage />, { route: "/incidents", me: makeMe(["incident.read"], "viewer") });
    await screen.findByText("No incidents yet");
    expect(screen.queryByRole("button", { name: "New incident" })).not.toBeInTheDocument();
  });
});

describe("IncidentDetailPage", () => {
  it("shows a 409 invalid_transition from the server", async () => {
    const detail: IncidentDetail = { ...incident("i1"), investigations: 0, latest_investigation: null };
    const fetchMock = mockFetch([
      ["GET", /^\/api\/v1\/incidents\/i1$/, () => jsonResponse(detail)],
      ["GET", /^\/api\/v1\/incidents\/i1\/alerts$/, () => jsonResponse({ items: [], next_cursor: null })],
      ["GET", /^\/api\/v1\/incidents\/i1\/investigations$/, () => jsonResponse([])],
      ["GET", /^\/api\/v1\/audit$/, () => jsonResponse({ items: [], next_cursor: null })],
      [
        "PATCH",
        /^\/api\/v1\/incidents\/i1$/,
        () =>
          jsonResponse(
            { error: { code: "invalid_transition", message: "An incident cannot move from open to resolved", request_id: "req-9", details: [] } },
            409,
          ),
      ],
    ]);
    renderWithProviders(<IncidentDetailPage />, { route: "/incidents/i1", path: "/incidents/:incidentId" });
    await screen.findByRole("heading", { name: "Checkout errors i1", level: 1 });
    await userEvent.click(screen.getByRole("button", { name: "Resolve" }));
    expect(await screen.findByText("An incident cannot move from open to resolved")).toBeInTheDocument();
    const patch = fetchMock.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === "PATCH");
    expect(new Headers((patch?.[1] as RequestInit).headers).get("X-Aegis-CSRF")).toBe("1");
    expect((patch?.[1] as RequestInit).body).toBe(JSON.stringify({ status: "resolved" }));
    await waitFor(() => expect(screen.getByText("req-9")).toBeInTheDocument());
  });
});
