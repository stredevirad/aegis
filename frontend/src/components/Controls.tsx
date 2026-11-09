import { useId, useRef, useState, type ReactNode } from "react";

/** A destructive action that needs a second, explicit click. */
export function ConfirmButton({
  label,
  confirmLabel,
  prompt,
  onConfirm,
  disabled,
  pending,
  className = "btn btn-small btn-danger",
}: {
  label: string;
  confirmLabel?: string;
  prompt?: ReactNode;
  onConfirm: () => void;
  disabled?: boolean;
  pending?: boolean;
  className?: string;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <button type="button" className={className} disabled={disabled || pending} onClick={() => setArmed(true)}>
        {pending ? "Working…" : label}
      </button>
    );
  }
  return (
    <span className="confirm" role="group" aria-label={`Confirm: ${label}`}>
      {prompt ? <span className="confirm-prompt">{prompt}</span> : null}
      <button
        type="button"
        className="btn btn-small btn-danger"
        autoFocus
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {confirmLabel ?? `Yes, ${label.toLowerCase()}`}
      </button>
      <button type="button" className="btn btn-small" onClick={() => setArmed(false)}>
        Cancel
      </button>
    </span>
  );
}

/** A secret shown exactly once. It lives only in component state and is never persisted. */
export function SecretOnce({
  title,
  secret,
  onDismiss,
  children,
}: {
  title: string;
  secret: string;
  onDismiss: () => void;
  children?: ReactNode;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [copyState, setCopyState] = useState<"idle" | "copied" | "manual">("idle");

  async function copy() {
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error("no clipboard");
      await navigator.clipboard.writeText(secret);
      setCopyState("copied");
    } catch {
      inputRef.current?.focus();
      inputRef.current?.select();
      setCopyState("manual");
    }
  }

  return (
    <div className="secret-once" role="region" aria-label={title}>
      <p className="notice-title">{title}</p>
      <p>
        This is the only time the secret is shown. Store it in your secret manager now; Aegis keeps
        only a hash and cannot show it again.
      </p>
      {children}
      <label htmlFor={inputId} className="visually-hidden">
        Secret
      </label>
      <div className="secret-row">
        <input
          id={inputId}
          ref={inputRef}
          className="secret-value"
          readOnly
          value={secret}
          spellCheck={false}
          autoComplete="off"
          onFocus={(e) => e.currentTarget.select()}
        />
        <button type="button" className="btn btn-small" onClick={() => void copy()}>
          Copy
        </button>
        <button type="button" className="btn btn-small btn-primary" onClick={onDismiss}>
          I have stored it — dismiss
        </button>
      </div>
      <p className="small" aria-live="polite">
        {copyState === "copied"
          ? "Copied to the clipboard."
          : copyState === "manual"
            ? "Clipboard unavailable here: the secret is selected, press Ctrl+C (⌘C) to copy."
            : ""}
      </p>
    </div>
  );
}

export function LoadMore({
  hasNextPage,
  isFetchingNextPage,
  onLoadMore,
  shown,
  noun = "items",
}: {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
  shown: number;
  noun?: string;
}) {
  return (
    <div className="load-more">
      <span className="muted small" aria-live="polite">
        Showing {shown} {noun}
        {hasNextPage ? "" : shown > 0 ? " (all loaded)" : ""}
      </span>
      {hasNextPage ? (
        <button type="button" className="btn btn-small" onClick={onLoadMore} disabled={isFetchingNextPage}>
          {isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      ) : null}
    </div>
  );
}

export function RemediationPlaceholder() {
  return (
    <section className="panel remediation-placeholder" aria-labelledby="remediation-heading">
      <div className="panel-head">
        <h2 id="remediation-heading" className="panel-title">
          Remediation
        </h2>
      </div>
      <div className="panel-body">
        <p>
          Remediation proposals are not yet available. When they are, every action will need an
          explicit human approval before it runs, and remediation must also be enabled for the
          organization.
        </p>
      </div>
    </section>
  );
}
