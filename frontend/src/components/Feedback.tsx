import type { ReactNode } from "react";
import { isApiError } from "../api/client";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <div className="empty-body">{children}</div> : null}
    </div>
  );
}

/** Shows an API error: the server's message (as text), its code and the request ID. */
export function ErrorNotice({
  error,
  title,
  onRetry,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}) {
  let heading = title;
  let message = "Something went wrong.";
  let code: string | null = null;
  let requestId: string | null = null;
  let details: string[] = [];
  if (isApiError(error)) {
    message = error.message;
    code = error.code;
    requestId = error.requestId;
    details = error.details
      .map((d) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
        const msg = typeof d.msg === "string" ? d.msg : "";
        return [loc, msg].filter(Boolean).join(": ");
      })
      .filter(Boolean)
      .slice(0, 8);
    if (error.status === 403 && code === "forbidden") {
      heading = heading ?? "Not permitted";
    }
    if (error.retryAfter) {
      details = [...details, `Retry after ${error.retryAfter} s.`];
    }
  } else if (error instanceof Error) {
    message = error.message;
  }
  return (
    <div className="notice notice-error" role="alert">
      {heading ? <p className="notice-title">{heading}</p> : null}
      <p>{message}</p>
      {details.length ? (
        <ul className="notice-details">
          {details.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      ) : null}
      {code || requestId ? (
        <p className="notice-meta">
          {code ? (
            <>
              Code <code>{code}</code>
            </>
          ) : null}
          {code && requestId ? " · " : null}
          {requestId ? (
            <>
              Request ID <code className="selectable">{requestId}</code> (quote it when contacting
              support)
            </>
          ) : null}
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
  role,
}: {
  tone?: "info" | "warning" | "error" | "success";
  title?: string;
  children?: ReactNode;
  role?: "status" | "alert" | "note";
}) {
  return (
    <div className={`notice notice-${tone}`} role={role}>
      {title ? <p className="notice-title">{title}</p> : null}
      {children}
    </div>
  );
}
