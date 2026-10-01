# Title: Redaction, egress policy, credential encryption and retention
# Status: Accepted
# Date: 2026-10-01
# Source: production-readiness audit; Council A (docs/councils/council-A-architecture.md)

## Decision
* **Redaction** (pure functions, unit-tested): bearer tokens, JWTs, cloud keys, private-key blocks, passwords in URLs and key=value pairs, emails, card numbers (Luhn), applied at collection time before storage and again before prompting. Evidence records its redaction state.
* **Egress**: one HTTP client factory for all outbound calls: http/https only, DNS resolved once and pinned, link-local/metadata always denied, loopback/private denied unless the deployment allowlists them, redirects disabled, response size caps, timeouts. Credentials never travel in URLs.
* **LLM egress**: per-organization provider allowlist; cloud providers are opt-in per organization; no automatic local-to-cloud fallback.
* **Credentials**: integration and provider secrets encrypted at rest with a key from the environment or a secret manager (key IDs allow rotation); never returned by the API after creation.
* **Retention**: per-organization policy for raw snapshot payloads, evidence, LLM call records and audit events, enforced by a worker sweep; audit retention has a floor.
