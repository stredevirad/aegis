import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import type { Me } from "../api/types";
import { MeProvider } from "../auth/context";

export const ADMIN_PERMISSIONS = [
  "apikey.manage",
  "audit.read",
  "change.read",
  "change.write",
  "evidence.read",
  "incident.read",
  "incident.write",
  "integration.manage",
  "integration.read",
  "investigation.cancel",
  "investigation.read",
  "investigation.start",
  "member.manage",
  "org.manage",
  "org.read",
];

export function makeMe(permissions: string[] = ADMIN_PERMISSIONS, role = "admin"): Me {
  return {
    kind: "user",
    org: { id: "org-1", slug: "demo", name: "Demo", role },
    user_id: "user-1",
    email: "admin@example.com",
    display_name: null,
    role,
    permissions,
    memberships: [{ id: "org-1", slug: "demo", name: "Demo", role }],
    api_key_prefix: null,
  };
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", path = "*", me = makeMe() }: { route?: string; path?: string; me?: Me } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <MeProvider me={me}>
          <Routes>
            <Route path={path} element={ui} />
          </Routes>
        </MeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Request-ID": "req-test", ...headers },
  });
}

type Handler = (url: URL, init: RequestInit | undefined) => Response | Promise<Response>;

/** Stubs fetch with a list of [method, path regex, handler]; unmatched requests return 404. */
export function mockFetch(routes: [string, RegExp, Handler][]) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    for (const [m, re, handler] of routes) {
      if (m === method && re.test(url.pathname)) return handler(url, init);
    }
    return jsonResponse({ error: { code: "not_found", message: "not mocked", request_id: "r", details: [] } }, 404);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
