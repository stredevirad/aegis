import type { ReactNode } from "react";
import { safeUrl } from "../lib/safeUrl";

/** A link built from an untrusted URL (e.g. an alert's generator_url). Only absolute http(s)
 * URLs without credentials become links; anything else is shown as inert text. */
export function SafeExternalLink({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  const url = safeUrl(href);
  if (!url) {
    if (!href) return <span className="muted">—</span>;
    return (
      <span className="muted" title="Not linked: only absolute http(s) URLs are opened">
        (link withheld)
      </span>
    );
  }
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="external">
      {children}
      <span className="visually-hidden"> (opens in a new tab)</span>
    </a>
  );
}
