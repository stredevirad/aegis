import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiRequest, buildQuery, setUnauthenticatedHandler } from "./client";
import { jsonResponse } from "../test/utils";

function lastCall(fn: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const call = fn.mock.calls[fn.mock.calls.length - 1];
  if (!call) throw new Error("fetch was not called");
  return { url: String(call[0]), init: (call[1] ?? {}) as RequestInit };
}

function headersOf(init: RequestInit): Headers {
  return new Headers(init.headers);
}

describe("api client", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let onUnauthenticated: ReturnType<typeof vi.fn<() => void>>;
  let restore: () => void;

  beforeEach(() => {
    fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    onUnauthenticated = vi.fn<() => void>();
    restore = setUnauthenticatedHandler(onUnauthenticated);
  });

  afterEach(() => {
    setUnauthenticatedHandler(restore);
  });

  it("GET sends same-origin credentials and no CSRF header", async () => {
    await api.get("/api/v1/incidents");
    const { init } = lastCall(fetchMock);
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
    const h = headersOf(init);
    expect(h.has("X-Aegis-CSRF")).toBe(false);
    expect(h.has("Content-Type")).toBe(false);
    expect(init.body).toBeUndefined();
  });

  it.each([
    ["POST", () => api.post("/api/v1/incidents", { title: "x", severity: "high" })],
    ["PATCH", () => api.patch("/api/v1/incidents/1", { status: "resolved" })],
    ["DELETE", () => api.del("/api/v1/api-keys/1")],
  ] as const)("%s sends X-Aegis-CSRF: 1 and a JSON content type", async (method, call) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(null, 204));
    await call();
    const { init } = lastCall(fetchMock);
    expect(init.method).toBe(method);
    expect(init.credentials).toBe("same-origin");
    const h = headersOf(init);
    expect(h.get("X-Aegis-CSRF")).toBe("1");
    expect(h.get("Content-Type")).toBe("application/json");
  });

  it("serialises the JSON body and extra headers", async () => {
    await api.post("/api/v1/incidents/1/investigations", { source: { kind: "live" } }, {
      headers: { "Idempotency-Key": "abc" },
    });
    const { init } = lastCall(fetchMock);
    expect(init.body).toBe(JSON.stringify({ source: { kind: "live" } }));
    expect(headersOf(init).get("Idempotency-Key")).toBe("abc");
  });

  it("encodes repeated query parameters and skips empty ones", () => {
    expect(buildQuery({ status: ["open", "investigating"], q: "", service: undefined, limit: 50 })).toBe(
      "?status=open&status=investigating&limit=50",
    );
    expect(buildQuery({ q: "a&b=c" })).toBe("?q=a%26b%3Dc");
  });

  it("maps the stable error schema to ApiError", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: "invalid_transition",
            message: "An incident cannot move from closed to open",
            request_id: "req-123",
            details: [{ loc: ["body", "status"], msg: "bad", type: "x" }],
          },
        },
        409,
      ),
    );
    const err = await apiRequest("/api/v1/incidents/1", { method: "PATCH", body: { status: "open" } }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    const e = err as ApiError;
    expect(e.status).toBe(409);
    expect(e.code).toBe("invalid_transition");
    expect(e.message).toBe("An incident cannot move from closed to open");
    expect(e.requestId).toBe("req-123");
    expect(e.details).toHaveLength(1);
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it("falls back to a generic message and the X-Request-ID header for non-JSON errors", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("<html>bad gateway</html>", { status: 502, headers: { "X-Request-ID": "edge-1" } }),
    );
    const e = (await api.get("/api/v1/me").catch((x: unknown) => x)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(502);
    expect(e.message).toMatch(/unavailable/i);
    expect(e.message).not.toContain("<html>");
    expect(e.requestId).toBe("edge-1");
  });

  it("reports Retry-After on 429", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "queue_full", message: "Queue full", request_id: "r", details: [] } }, 429, {
        "Retry-After": "30",
      }),
    );
    const e = (await api.post("/x").catch((x: unknown) => x)) as ApiError;
    expect(e.code).toBe("queue_full");
    expect(e.retryAfter).toBe(30);
  });

  it("calls the unauthenticated handler and throws on 401", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "unauthenticated", message: "Session expired or revoked", request_id: "r1", details: [] } },
        401,
      ),
    );
    await expect(api.get("/api/v1/incidents")).rejects.toMatchObject({ status: 401, code: "unauthenticated" });
    expect(onUnauthenticated).toHaveBeenCalledTimes(1);
  });

  it("does not redirect on 401 when the caller opts out (login page)", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "unauthenticated", message: "no", request_id: "r", details: [] } }, 401),
    );
    await expect(api.get("/api/v1/me", undefined, { redirectOn401: false })).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthenticated).not.toHaveBeenCalled();
  });

  it("the default 401 handler sends the browser to /login with the current path", async () => {
    setUnauthenticatedHandler(restore); // the real handler
    const assign = vi.fn();
    vi.stubGlobal("location", { pathname: "/incidents/abc", search: "?x=1", assign });
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "unauthenticated", message: "no", request_id: "r", details: [] } }, 401),
    );
    await expect(api.get("/api/v1/incidents")).rejects.toBeInstanceOf(ApiError);
    expect(assign).toHaveBeenCalledWith("/login?return_to=%2Fincidents%2Fabc%3Fx%3D1");
  });

  it("returns undefined for 204 and maps network failures", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(null, 204));
    await expect(api.del("/api/v1/invitations/1")).resolves.toBeUndefined();
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api.get("/api/v1/me")).rejects.toMatchObject({ code: "network_error", status: 0 });
  });
});
