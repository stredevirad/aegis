import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { InvestigationEvent } from "../api/types";
import { deriveProgress } from "../lib/steps";
import { ProgressView } from "../pages/investigation/Progress";
import { jsonResponse } from "../test/utils";
import { parseStreamEvent, useInvestigationStream } from "./useInvestigationStream";

class MockEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: MockEventSource[] = [];
  readonly url: string;
  readyState = MockEventSource.CONNECTING;
  closed = false;
  private listeners = new Map<string, Set<(e: Event) => void>>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: Event) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }
  removeEventListener(type: string, fn: (e: Event) => void) {
    this.listeners.get(type)?.delete(fn);
  }
  close() {
    this.readyState = MockEventSource.CLOSED;
    this.closed = true;
  }
  private dispatch(type: string, event: Event) {
    this.listeners.get(type)?.forEach((fn) => fn(event));
  }
  // --- test helpers
  open() {
    this.readyState = MockEventSource.OPEN;
    act(() => this.dispatch("open", new Event("open")));
  }
  emit(event: InvestigationEvent) {
    act(() =>
      this.dispatch(event.kind, new MessageEvent(event.kind, { data: JSON.stringify(event), lastEventId: String(event.seq) })),
    );
  }
  fail(readyState: number) {
    this.readyState = readyState;
    act(() => this.dispatch("error", new Event("error")));
  }
}

let seqTs = 0;
function ev(seq: number, kind: string, data: Record<string, unknown> = {}, message = kind): InvestigationEvent {
  seqTs += 1;
  return {
    seq,
    ts: new Date(Date.UTC(2026, 9, 1, 10, 0, seqTs)).toISOString(),
    kind,
    state: null,
    actor: null,
    message,
    data,
  };
}

function statuses(events: InvestigationEvent[]): Record<string, string> {
  return Object.fromEntries(deriveProgress(events).steps.map((s) => [s.key, s.status]));
}

describe("useInvestigationStream", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("follows the steps over SSE and stops at the terminal event", () => {
    const onTerminal = vi.fn();
    const { result } = renderHook(() => useInvestigationStream("inv-1", { live: true, onTerminal }));
    expect(MockEventSource.instances).toHaveLength(1);
    const es = MockEventSource.instances[0]!;
    // the first connection has no ?after= so the browser's reconnects resume with Last-Event-ID
    expect(es.url).toBe("/api/v1/investigations/inv-1/stream");
    expect(result.current.mode).toBe("connecting");

    es.open();
    expect(result.current.mode).toBe("live");

    es.emit(ev(1, "queued"));
    expect(statuses(result.current.events)).toMatchObject({ queued: "done", claimed: "active", outcome: "pending" });

    es.emit(ev(2, "claimed", { attempt: 1 }));
    es.emit(ev(3, "collecting"));
    expect(statuses(result.current.events)).toMatchObject({ collecting: "done", collected: "active" });

    es.emit(ev(4, "collected", { modality: "metrics", provider: "synthetic", status: "ok", items: 12 }));
    es.emit(ev(5, "collected", { modality: "logs", provider: "synthetic", status: "failed", items: 0 }));
    es.emit(ev(6, "snapshot_stored"));
    const collected = deriveProgress(result.current.events).steps.find((s) => s.key === "collected");
    expect(collected?.collected?.map((c) => [c.modality, c.status])).toEqual([
      ["metrics", "ok"],
      ["logs", "failed"],
    ]);
    expect(statuses(result.current.events)).toMatchObject({ snapshot_stored: "done", analyzing: "active" });

    es.emit(ev(7, "analyzing"));
    es.emit(ev(8, "hypotheses"));
    es.emit(ev(9, "diagnosis", { classification: "CONFIRMED" }));
    expect(onTerminal).not.toHaveBeenCalled();
    expect(es.closed).toBe(false);

    es.emit(ev(10, "completed"));
    expect(result.current.terminal).toBe("completed");
    expect(result.current.mode).toBe("ended");
    expect(onTerminal).toHaveBeenCalledTimes(1);
    expect(onTerminal).toHaveBeenCalledWith("completed");
    // closed at once so the browser does not reconnect to a finished stream
    expect(es.closed).toBe(true);
    expect(statuses(result.current.events)).toMatchObject({
      queued: "done",
      claimed: "done",
      collecting: "done",
      collected: "done",
      snapshot_stored: "done",
      analyzing: "done",
      hypotheses: "done",
      diagnosis: "done",
      outcome: "done",
    });
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it("drops duplicate events replayed after a reconnect and lets the browser reconnect", () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1", { live: true }));
    const es = MockEventSource.instances[0]!;
    es.open();
    es.emit(ev(1, "queued"));
    es.emit(ev(2, "claimed"));
    // the server closed the stream (5-minute limit): the browser is reconnecting
    es.fail(MockEventSource.CONNECTING);
    expect(result.current.mode).toBe("reconnecting");
    expect(es.closed).toBe(false);
    es.open();
    expect(result.current.mode).toBe("live");
    es.emit(ev(2, "claimed")); // replayed
    es.emit(ev(3, "collecting"));
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3]);
    expect(MockEventSource.instances).toHaveLength(1);
  });

  it("marks failed and cancelled outcomes and leaves later steps not reached", () => {
    const { result } = renderHook(() => useInvestigationStream("inv-1", { live: true }));
    const es = MockEventSource.instances[0]!;
    es.open();
    es.emit(ev(1, "queued"));
    es.emit(ev(2, "claimed"));
    es.emit(ev(3, "collecting"));
    es.emit(ev(4, "failed", {}, "all telemetry backends failed"));
    const p = deriveProgress(result.current.events);
    expect(p.terminal).toBe("failed");
    const s = statuses(result.current.events);
    expect(s).toMatchObject({ collecting: "done", collected: "not_reached", analyzing: "not_reached", outcome: "failed" });
    expect(p.steps.find((x) => x.key === "outcome")?.message).toBe("all telemetry backends failed");
    expect(es.closed).toBe(true);
  });

  it("falls back to polling when the server refuses the stream", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      const after = Number(url.searchParams.get("after"));
      if (after < 2) return jsonResponse([ev(2, "claimed"), ev(3, "analyzing")]);
      return jsonResponse([ev(4, "completed")]);
    });
    vi.stubGlobal("fetch", fetchMock);
    const onTerminal = vi.fn();
    const { result } = renderHook(() =>
      useInvestigationStream("inv-1", { live: true, onTerminal, pollIntervalMs: 5 }),
    );
    const es = MockEventSource.instances[0]!;
    es.open();
    es.emit(ev(1, "queued"));
    es.fail(MockEventSource.CLOSED); // e.g. 429 too_many_streams: no automatic retry
    expect(es.closed).toBe(true);
    expect(result.current.mode).toBe("polling");
    await waitFor(() => expect(result.current.terminal).toBe("completed"));
    expect(result.current.events.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain("/api/v1/investigations/inv-1/events?after=1");
    expect(onTerminal).toHaveBeenCalledTimes(1);
    const calls = fetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 30));
    expect(fetchMock.mock.calls.length).toBe(calls); // polling stopped after the terminal event
    expect(result.current.mode).toBe("ended");
  });

  it("falls back to polling after repeated stream errors", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])));
    const { result } = renderHook(() =>
      useInvestigationStream("inv-1", { live: true, maxStreamErrors: 3, pollIntervalMs: 50 }),
    );
    const es = MockEventSource.instances[0]!;
    es.fail(MockEventSource.CONNECTING);
    es.fail(MockEventSource.CONNECTING);
    expect(result.current.mode).toBe("reconnecting");
    es.fail(MockEventSource.CONNECTING);
    expect(result.current.mode).toBe("polling");
    expect(es.closed).toBe(true);
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());
  });

  it("loads the history over REST for a finished investigation without opening a stream", async () => {
    const fetchMock = vi.fn(async () => jsonResponse([ev(1, "queued"), ev(2, "claimed"), ev(3, "cancelled")]));
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useInvestigationStream("inv-9", { live: false }));
    await waitFor(() => expect(result.current.mode).toBe("ended"));
    expect(MockEventSource.instances).toHaveLength(0);
    expect(result.current.terminal).toBe("cancelled");
    expect(statuses(result.current.events)).toMatchObject({ claimed: "done", collecting: "not_reached", outcome: "cancelled" });
  });

  it("ignores malformed stream payloads", () => {
    expect(parseStreamEvent("not json")).toBeNull();
    expect(parseStreamEvent(JSON.stringify({ seq: "1", kind: "queued" }))).toBeNull();
    expect(parseStreamEvent(JSON.stringify({ seq: 1, kind: "queued", data: [] }))?.data).toEqual({});
  });
});

describe("deriveProgress", () => {
  it("uses only the latest attempt after a retry and shows a reused snapshot as skipped", () => {
    const events = [
      ev(1, "queued"),
      ev(2, "claimed", { attempt: 1 }),
      ev(3, "collecting"),
      ev(4, "collected", { modality: "metrics", provider: "prom", status: "ok", items: 3 }),
      ev(5, "snapshot_stored"),
      ev(6, "retry_scheduled", {}, "Attempt 1 failed; retrying in 4s"),
    ];
    let p = deriveProgress(events);
    expect(p.retryScheduled?.message).toBe("Attempt 1 failed; retrying in 4s");
    expect(p.steps.some((s) => s.status === "active")).toBe(false);

    events.push(ev(7, "claimed", { attempt: 2 }), ev(8, "snapshot_stored", {}, "Reusing the snapshot stored by an earlier attempt"));
    p = deriveProgress(events);
    expect(p.attempt).toBe(2);
    expect(p.retryScheduled).toBeNull();
    const s = Object.fromEntries(p.steps.map((x) => [x.key, x.status]));
    expect(s).toMatchObject({ claimed: "done", collecting: "skipped", collected: "skipped", snapshot_stored: "done", analyzing: "active" });
  });

  it("adds the AI assessment step only when it happened", () => {
    expect(deriveProgress([ev(1, "queued")]).steps.some((s) => s.key === "ai_adjudication")).toBe(false);
    const p = deriveProgress([ev(1, "queued"), ev(2, "claimed"), ev(3, "diagnosis"), ev(4, "ai_adjudication", { status: "accepted" })]);
    expect(p.steps.find((s) => s.key === "ai_adjudication")?.status).toBe("done");
  });
});

describe("ProgressView", () => {
  it("announces the latest event in a polite live region", () => {
    render(<ProgressView events={[ev(1, "queued"), ev(2, "claimed", {}, "Claimed by worker (attempt 1)")]} mode="live" error={null} />);
    const live = document.querySelector('[aria-live="polite"]');
    expect(live?.textContent).toBe("claimed: Claimed by worker (attempt 1)");
    expect(screen.getByRole("list", { name: "Investigation steps" })).toBeInTheDocument();
    expect(screen.getByText("Live")).toBeInTheDocument();
  });
});
