import { useEffect, useEffectEvent, useRef, useState } from "react";
import { isApiError } from "../api/client";
import { fetchEvents, investigationPath } from "../api/queries";
import type { InvestigationEvent } from "../api/types";
import { EVENT_KINDS, TERMINAL_EVENT_KINDS } from "../lib/steps";

// Live investigation progress (ADR-010).
//
// * `live`: Server-Sent Events on /investigations/{id}/stream. Events are named by kind, so a
//   listener is registered per kind. The first connection carries no `?after=` so the browser's
//   own reconnects resume with `Last-Event-ID`; a stream created later (fallback recovery) uses
//   `?after=<last seq>`. Duplicates are dropped by sequence number.
// * The server closes the stream after a terminal event or after 5 minutes. After a terminal
//   event the EventSource is closed at once (no reconnect); otherwise the browser reconnects.
// * A stream refused by the server (non-200, e.g. 401 or 429 too_many_streams → readyState
//   CLOSED) or `maxStreamErrors` consecutive errors switch to polling /events?after=.
// * Not live (terminal investigation): the history is fetched once over REST.
//
// The hook keeps its state per mounted component: give the consumer `key={investigationId}`.

export type StreamMode = "connecting" | "live" | "reconnecting" | "polling" | "history" | "ended";

export interface StreamOptions {
  live: boolean;
  onEvent?: (event: InvestigationEvent) => void;
  onTerminal?: (kind: string) => void;
  pollIntervalMs?: number;
  maxStreamErrors?: number;
}

export interface StreamState {
  events: InvestigationEvent[];
  mode: StreamMode;
  terminal: string | null;
  error: string | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Validates the shape of an event received from the stream (untrusted JSON). */
export function parseStreamEvent(raw: string): InvestigationEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(value)) return null;
  const { seq, ts, kind, state, actor, message, data } = value;
  if (typeof seq !== "number" || !Number.isInteger(seq) || typeof kind !== "string") return null;
  return {
    seq,
    ts: typeof ts === "string" ? ts : "",
    kind,
    state: typeof state === "string" ? state : null,
    actor: typeof actor === "string" ? actor : null,
    message: typeof message === "string" ? message : "",
    data: isRecord(data) ? data : {},
  };
}

export function useInvestigationStream(investigationId: string, options: StreamOptions): StreamState {
  const { live, pollIntervalMs = 2000, maxStreamErrors = 3 } = options;
  const [events, setEvents] = useState<InvestigationEvent[]>([]);
  const [mode, setMode] = useState<StreamMode>(live ? "connecting" : "history");
  const [terminal, setTerminal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const lastSeq = useRef(0);
  const terminalKind = useRef<string | null>(null);

  const notifyEvent = useEffectEvent((e: InvestigationEvent) => options.onEvent?.(e));
  const notifyTerminal = useEffectEvent((kind: string) => options.onTerminal?.(kind));

  const ingest = useEffectEvent((batch: InvestigationEvent[]) => {
    const fresh = batch.filter((e) => e.seq > lastSeq.current).sort((a, b) => a.seq - b.seq);
    if (fresh.length === 0) return;
    lastSeq.current = fresh[fresh.length - 1]!.seq;
    setEvents((prev) => [...prev, ...fresh]);
    for (const e of fresh) {
      notifyEvent(e);
      if (TERMINAL_EVENT_KINDS.has(e.kind) && !terminalKind.current) {
        terminalKind.current = e.kind;
        setTerminal(e.kind);
        notifyTerminal(e.kind);
      }
    }
  });

  // Live: SSE with polling fallback.
  useEffect(() => {
    if (!live || terminalKind.current) return;
    let disposed = false;
    let source: EventSource | null = null;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;
    let consecutiveErrors = 0;

    const closeSource = () => {
      source?.close();
      source = null;
    };

    const startPolling = () => {
      closeSource();
      setMode("polling");
      const tick = async () => {
        if (disposed || terminalKind.current) return;
        try {
          const batch = await fetchEvents(investigationId, lastSeq.current);
          if (disposed) return;
          ingest(batch);
          setError(null);
        } catch (e) {
          if (disposed) return;
          setError(isApiError(e) ? e.message : "Polling for progress failed");
        }
        if (disposed) return;
        if (terminalKind.current) setMode("ended");
        else pollTimer = setTimeout(() => void tick(), pollIntervalMs);
      };
      void tick();
    };

    const onMessage = (msg: Event) => {
      const data = (msg as MessageEvent).data;
      if (typeof data !== "string") return;
      const event = parseStreamEvent(data);
      if (!event) return;
      consecutiveErrors = 0;
      ingest([event]);
      if (terminalKind.current) {
        closeSource(); // the server is about to close: do not let the browser reconnect
        setMode("ended");
      }
    };

    const open = () => {
      const url =
        `${investigationPath(investigationId)}/stream` +
        (lastSeq.current > 0 ? `?after=${lastSeq.current}` : "");
      let es: EventSource;
      try {
        es = new EventSource(url);
      } catch {
        startPolling();
        return;
      }
      source = es;
      es.addEventListener("open", () => {
        if (disposed || source !== es) return;
        consecutiveErrors = 0;
        setMode("live");
        setError(null);
      });
      for (const kind of EVENT_KINDS) es.addEventListener(kind, onMessage);
      es.addEventListener("message", onMessage);
      es.addEventListener("error", () => {
        if (disposed || source !== es) return;
        if (terminalKind.current) {
          closeSource();
          setMode("ended");
          return;
        }
        consecutiveErrors += 1;
        if (es.readyState === EventSource.CLOSED || consecutiveErrors >= maxStreamErrors) {
          setError("The live stream is unavailable; polling for progress instead.");
          startPolling();
          return;
        }
        setMode("reconnecting"); // the browser reconnects and sends Last-Event-ID
      });
    };

    open();
    return () => {
      disposed = true;
      closeSource();
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [investigationId, live, pollIntervalMs, maxStreamErrors]);

  // Not live: fetch whatever is missing once (also closes the gap if the investigation became
  // terminal before the stream delivered its last events).
  useEffect(() => {
    if (live || terminalKind.current) return;
    let cancelled = false;
    const load = async () => {
      try {
        for (let page = 0; page < 20; page++) {
          const batch = await fetchEvents(investigationId, lastSeq.current);
          if (cancelled) return;
          ingest(batch);
          if (batch.length < 500) break;
        }
        setError(null);
      } catch (e) {
        if (!cancelled) setError(isApiError(e) ? e.message : "Could not load the event history");
      }
      if (!cancelled) setMode("ended");
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [investigationId, live]);

  return { events, mode, terminal, error };
}
