// The single HTTP client for /api/v1 and /auth (security model C2, C19).
//
// * Same-origin cookies only (the session cookie is HttpOnly; nothing is stored client-side).
// * Every non-GET request carries `X-Aegis-CSRF: 1` and `Content-Type: application/json`.
// * Errors are parsed from the stable schema {"error": {code, message, request_id, details}}.
//   The message is untrusted text and must be rendered as text.
// * A 401 sends the browser to /login (unless the caller opts out, e.g. the login page itself).

export type QueryScalar = string | number | boolean;
export type QueryValue = QueryScalar | null | undefined | readonly QueryScalar[];
export type QueryParams = Record<string, QueryValue>;

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface RequestOptions {
  method?: HttpMethod;
  body?: unknown;
  query?: QueryParams;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Default true: a 401 response triggers the unauthenticated handler (redirect to /login). */
  redirectOn401?: boolean;
}

export interface ErrorDetail {
  loc?: string[];
  msg?: string;
  type?: string;
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly details: ErrorDetail[];
  readonly retryAfter: number | null;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null = null,
    details: ErrorDetail[] = [],
    retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
    this.retryAfter = retryAfter;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function isUnauthenticated(error: unknown): boolean {
  return isApiError(error) && error.status === 401;
}

export function isNotFound(error: unknown): boolean {
  return isApiError(error) && error.status === 404;
}

export function isForbidden(error: unknown): boolean {
  return isApiError(error) && error.status === 403;
}

// --- unauthenticated handling -----------------------------------------------------------------

export function loginPath(returnTo: string): string {
  return `/login?return_to=${encodeURIComponent(returnTo)}`;
}

function defaultUnauthenticatedHandler(): void {
  const { pathname, search } = window.location;
  if (pathname === "/login") return;
  window.location.assign(loginPath(pathname + search));
}

let unauthenticatedHandler: () => void = defaultUnauthenticatedHandler;

/** Replace the 401 handler (tests); returns the previous one. */
export function setUnauthenticatedHandler(handler: () => void): () => void {
  const previous = unauthenticatedHandler;
  unauthenticatedHandler = handler;
  return previous;
}

// --- request ----------------------------------------------------------------------------------

export function buildQuery(params: QueryParams | undefined): string {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value as readonly QueryScalar[]) search.append(key, String(v));
    } else {
      search.append(key, String(value));
    }
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

const GENERIC_MESSAGES: Record<number, string> = {
  400: "The request was rejected.",
  401: "Your session has expired. Sign in again.",
  403: "You do not have permission to do this.",
  404: "Not found.",
  409: "The request conflicts with the current state.",
  413: "The request is too large.",
  422: "The request is invalid.",
  429: "Too many requests. Try again shortly.",
  500: "The server reported an internal error.",
  502: "The Aegis API is unavailable (bad gateway).",
  503: "The Aegis API is unavailable.",
  504: "The Aegis API did not respond in time.",
};

function genericMessage(status: number): string {
  return GENERIC_MESSAGES[status] ?? `The request failed (HTTP ${status}).`;
}

function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  const headerRequestId = res.headers.get("X-Request-ID");
  const retryAfter = parseRetryAfter(res.headers.get("Retry-After"));
  let text = "";
  try {
    text = await res.text();
  } catch {
    // body unreadable: fall through to the generic message
  }
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null; // e.g. an HTML 502 page from a proxy
    }
  }
  const err = isRecord(body) && isRecord(body.error) ? body.error : null;
  if (err && typeof err.code === "string" && typeof err.message === "string") {
    const requestId = typeof err.request_id === "string" ? err.request_id : headerRequestId;
    const details = Array.isArray(err.details)
      ? (err.details.filter(isRecord) as ErrorDetail[])
      : [];
    return new ApiError(res.status, err.code, err.message, requestId, details, retryAfter);
  }
  return new ApiError(
    res.status,
    `http_${res.status}`,
    genericMessage(res.status),
    headerRequestId,
    [],
    retryAfter,
  );
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers = new Headers({ Accept: "application/json" });
  if (method !== "GET") {
    headers.set("X-Aegis-CSRF", "1");
    headers.set("Content-Type", "application/json");
  }
  for (const [k, v] of Object.entries(options.headers ?? {})) headers.set(k, v);

  const init: RequestInit = {
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
    signal: options.signal,
  };
  if (method !== "GET" && options.body !== undefined) {
    init.body = JSON.stringify(options.body);
  }

  let res: Response;
  try {
    res = await fetch(path + buildQuery(options.query), init);
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(
      0,
      "network_error",
      "The Aegis API could not be reached. Check your connection and try again.",
    );
  }

  if (!res.ok) {
    const error = await errorFromResponse(res);
    if (res.status === 401 && options.redirectOn401 !== false) unauthenticatedHandler();
    throw error;
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(
      res.status,
      "invalid_response",
      "The server returned a response that is not JSON.",
      res.headers.get("X-Request-ID"),
    );
  }
}

export const api = {
  get: <T>(path: string, query?: QueryParams, opts: Omit<RequestOptions, "method" | "query"> = {}) =>
    apiRequest<T>(path, { ...opts, method: "GET", query }),
  post: <T>(path: string, body?: unknown, opts: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...opts, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, opts: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...opts, method: "PATCH", body }),
  del: <T = void>(path: string, opts: Omit<RequestOptions, "method"> = {}) =>
    apiRequest<T>(path, { ...opts, method: "DELETE" }),
};

/** URL path segment from an untrusted value (IDs come from the API). */
export function seg(value: string): string {
  return encodeURIComponent(value);
}
