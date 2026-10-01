# Title: Operator-owned connector registry for all egress
# Status: Accepted
# Date: 2026-10-01
# Source: Council C (docs/councils/council-C-security.md); supersedes the egress part of ADR-015

## Context
Observability backends are almost always on private networks, so a deployment-wide private-range allowlist collapses to `10.0.0.0/8` in practice. A tenant admin who can type a URL could then point Aegis at another tenant's Loki, at Aegis's own database or API, at Ollama's unauthenticated admin endpoints, at the Kubernetes API, or at a host they control to capture a stored credential.

## Decision
* **Tenants never type URLs.** The deployment operator defines a connector registry (configuration file or operator-only API): each connector has an ID, kind (prometheus, loki, opensearch, jaeger, tempo, kubernetes, llm, webhook, slack, pagerduty, flag-service), exact scheme/host/port, optional base path, optional pinned CA bundle, and the organization(s) it is bound to.
* Organization admins create integrations that **reference a connector ID** and supply credentials. A credential is stored bound to `(org_id, connector_id)` and is only ever sent to that connector; the worker refuses to load a credential whose binding does not match the job's organization and the connector being called.
* The egress client resolves DNS once, verifies every resolved address, connects to the pinned address, and still verifies TLS against the original hostname. Always denied regardless of the registry: link-local and cloud metadata addresses, unspecified/multicast addresses, and Aegis's own infrastructure endpoints (database, API, worker, configured LLM admin ports). Loopback is denied outside the development profile. Redirects are disabled; responses are size-capped; timeouts are mandatory; credentials never travel in URLs.
* Configuration validation rejects registry entries that are CIDR ranges or wildcards, and entries that collide with Aegis's own endpoints.
* LLM endpoints, webhooks, Slack and PagerDuty destinations go through the same registry.

## Consequences
Adding a backend requires the operator, not a tenant admin. This is the intended trade-off.

## Allowed claim
"Aegis connects only to operator-registered endpoints and never to its own infrastructure or metadata addresses", backed by named tests. Forbidden: "SSRF-proof".
