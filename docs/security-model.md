# Aegis — Security Model and Threat Model

**Status:** design adopted 2026-10-01 after the production-readiness audit (with an independent adversarial review), Council A, and Council C ([transcript](councils/council-C-security.md)). The **Status** column in §6 records what is implemented and tested; a control is listed as `implemented` only when a named test exercises it.

---

## 1. What Aegis is, from an attacker's point of view

Aegis is a **deputy**: it holds credentials for observability backends, LLM providers, notification channels and one remediation target; it sits inside the customer's network; and it reads text written by people who may be attackers (log lines, span attributes, metric labels, alert annotations, MCP tool output, model output). Every disqualifying outcome comes from that position:

* reading or acting on another tenant's data;
* being steered into requests to places it should not reach (SSRF);
* presenting a confident diagnosis that the data does not support;
* changing a production system without a human-authored, approved action.

## 2. Trust boundaries

| Party | Trusted? | Consequence |
|---|---|---|
| Deployment operator (installs Aegis, owns config, connector registry, database, secrets) | **Trusted** | Can read everything and can recompute the audit hash chain. The audit chain and the two-person rule do **not** protect against the operator. |
| Identity provider (OIDC) | **Trusted** for authentication | A compromised IdP can mint identities; the two-person rule does not protect against it. |
| Database owner / migration role | **Trusted** | Same as operator. |
| Organization admin | Trusted within their organization only | Cannot add egress targets (ADR-016), cannot see other organizations. |
| Investigator / reviewer / viewer | Trusted for their role in their organization | Server-side RBAC; viewers cannot trigger egress or LLM spend. |
| Telemetry content, alert payloads, change-event payloads (from authenticated sources) | **Untrusted data** | Never interpreted as instructions; bounded; redacted; rendered as text. |
| MCP servers | **Untrusted** | Read-only, pinned, bounded, provenance re-stamped (ADR-017). |
| LLM provider | **Untrusted for correctness**, trusted with the redacted data the organization opted to send | Output validated and bounded by the deterministic layer; cloud providers opt-in per organization. Confidentiality from the provider is **not** claimed. |
| Other tenants in the same deployment | Untrusted | RLS + application scoping is defence in depth. **Mutually hostile tenants should use separate deployments.** |

## 3. Assets

Tenant telemetry and evidence; diagnoses and audit history; integration and LLM credentials; user sessions and API keys; the remediation capability (write access to a flag service); availability of investigations during an incident; the integrity of what operators are shown.

## 4. Threat actors and attack paths

| Actor | Attack paths considered |
|---|---|
| Author of telemetry in one tenant | Prompt injection via logs/attributes; forged error spikes to steer deterministic analysis (*cannot be fully prevented*; mitigated by multi-modality requirements and "never more confident than the data"); stored XSS via log fields, alert labels, `generatorURL`; query injection via service names; forged "new log signatures"; oversized payloads. |
| Holder of an ingest API key / an Alertmanager | Forged alerts and change events; queue flooding to starve other tenants; prompt text injection through annotations. |
| Hostile MCP server | Provenance spoofing; tool-description injection; schema drift; oversized results; built-in tool shadowing. |
| Viewer account | Privilege escalation; triggering investigations (egress/LLM spend); reading raw evidence of other incidents/tenants. |
| Organization admin in tenant A | SSRF via integration endpoints to tenant B's backends, Aegis's database/API, Ollama admin endpoints, Kubernetes API, metadata service; credential capture by pairing a stored credential with an attacker host; exfiltration via webhook/Slack destinations. |
| LLM provider | Schema-valid but wrong output; data retention of prompts. |
| Network attacker between Aegis and a backend | TLS interception; DNS rebinding. |
| Browser-side attacker | XSS on the approval screen (defeats CSRF defence); CSRF; session theft. |

## 5. Confirmed attacks on the research prototype (must not recur)

From the audit's Appendix A: evidence takeover across incidents (S-C1); unscoped evidence listing (S-C2); PromQL selector breakout and Lucene filter neutralisation (S-C3); prompt-injection delivery with forged `=== SYSTEM ===` lines (S-H1); fabricated citations (S-H2); verification skipped yet CONFIRMED (S-H3); credentials from endpoint URLs leaking into prompts/DB (S-H4); MCP provenance spoofing (S-H5); Jaeger path/parameter injection (S-H6); first-enum-match classification (S-M1); unbounded ingestion (S-M2); tool budget overrun (S-M3); fail-open MCP allowlist (S-M4); concurrent write loss (S-M5); secrets persisted verbatim (S-M6); built-in tool shadowing (S-L1). Each becomes a test in `backend/tests/security/` (hostile suite) that blocks merges.

## 6. Controls

| # | Control | Threats addressed | Design reference | Status |
|---|---|---|---|---|
| C1 | OIDC BFF sessions (HttpOnly/Secure/SameSite=Lax), idle + absolute expiry, membership revalidated per request, revocation on membership removal | session theft, stale access | ADR-011 | implemented — `tests/api/test_security.py` (logout, role change, membership removal, idle/absolute expiry), `tests/db/test_cli.py` (disable user) |
| C2 | CSRF: Origin check + required custom header on state-changing requests | CSRF | ADR-011 | implemented — `test_security.py::test_csrf_is_required_for_cookie_authenticated_writes` |
| C3 | API keys: prefixed, HMAC-SHA-256 with pepper, org-scoped, role + scopes, expiry; ingest keys determine the org | forged ingestion, cross-tenant ingestion | ADR-011 | implemented — `test_security.py::test_api_key_shown_once_hashed_rotated_and_revoked`, `test_ingest_key_is_not_an_admin_or_reader`, `test_payload_cannot_choose_the_organization` |
| C4 | Identity→org binding by invitation or operator mapping; no auto-join on unverified email | cross-tenant access via IdP claims | ADR-011 A1 | implemented — `test_security.py::test_invitation_flow_requires_matching_verified_email`, `tests/api/test_oidc.py::test_unverified_email_does_not_accept_invitations` |
| C5 | RBAC matrix enforced server-side on every route; viewers cannot cause egress or LLM spend | privilege escalation | ADR-011 | implemented for M2 routes — `test_security.py::test_rbac_matrix`, `test_every_v1_route_declares_a_permission` |
| C6 | RLS with FORCE, NULLIF policies, non-owner app role, SET LOCAL, SECURITY DEFINER with search_path, security_invoker views, catalog test, tests as `aegis_app` | tenant leaks | ADR-012 | implemented — `tests/db/test_tenancy_and_schema.py`, `test_pool_isolation.py`, `test_security.py::test_cross_tenant_access_is_not_found_everywhere` |
| C7 | Evidence keyed by (org, incident, evidence); server-generated IDs for every persisted object; no upserts that can re-home rows | S-C1, S-M5 | ADR-008 | implemented — composite FKs + insert-only evidence (`test_tenancy_and_schema.py::test_child_rows_cannot_reference_another_tenants_parent`) |
| C8 | Connector registry; tenants pick connector IDs; credentials bound to (org, connector); pinned DNS, TLS to original hostname, always-deny list, no redirects, size caps, timeouts | SSRF, credential capture | ADR-016 | implemented — `tests/integrations/test_egress.py`, `tests/api/test_integrations.py` (tenant A cannot reach tenant B's backend, Aegis's DB/API, Ollama admin paths or metadata; copied credentials are not decrypted) |
| C9 | Queries built only from validated parameters (label-value escaping, configured metric names, hex trace IDs, URL-encoded path segments), never from free text | S-C3, S-H6 | ADR-008 | implemented — `tests/integrations/test_collectors.py` (hostile service names stay inside escaped matchers; OpenSearch uses structured filters; hex-only trace IDs) |
| C10 | Untrusted-data channel: telemetry only inside a delimited, labelled data block; control characters stripped; per-field caps; instructions only in the system role | S-H1 | ADR-009 | implemented — `tests/llm/test_prompt_and_providers.py` |
| C11 | Deterministic support level; model may only lower it; citation and membership validation; no fabricated citations | S-H2, S-H3, S-M1 | ADR-009 | implemented — `tests/llm/test_validate.py`, `tests/db/test_ai_adjudication.py` |
| C12 | Budgets checked before every external call; caps per response and per investigation; bounded ingestion everywhere | S-M2, S-M3 | ADR-010 | partial — ingestion bounds, per-response caps, prompt sizing, per-org daily token budget (`test_budget_and_capacity_limits`); no tool loop exists |
| C13 | Redaction at collection and before prompting; userinfo/query stripped from any URL that reaches logs, events or prompts; provider errors never forwarded verbatim to the model | S-H4, S-M6 | ADR-015 | implemented (heuristic) — `tests/test_redact.py`, collection-time redaction (`test_collectors.py`), prompt redaction (`test_prompt_redacts_and_bounds`), redacted logs |
| C14 | LLM provider allowlist per organization; cloud opt-in; no local→cloud fallback; output size caps; TLS verification | hostile/overreaching provider | ADR-015 | implemented — `test_cloud_provider_without_opt_in_sends_nothing`, settings validation (`tests/api/test_ai_api.py`); TLS via the egress client |
| C15 | Credentials encrypted at rest (key from environment/secret manager, key IDs), never returned after creation | credential theft from DB dumps | ADR-015 | implemented — `tests/integrations/test_crypto.py`, `test_credentials_are_write_only_and_encrypted` |
| C16 | MCP off by default, read-only, pinned descriptions/schemas, namespaced provenance, reserved built-in names, bounded results | S-H5, S-M4, S-L1 | ADR-017 | planned |
| C17 | Remediation: org catalog, model never chooses parameters, approval binds params hash + prior state + expiry, compare-and-set execution, state re-read, literal diff shown, inverse approved with forward, disabled by default | injected remediation, replay, stale approval | ADR-014 | planned |
| C18 | Audit: append-only (no UPDATE/DELETE grants), per-org hash chain; chain heads optionally emitted to the log stream | accidental/application-level tampering (not the operator) | ADR-010/015 | implemented — `test_tenancy_and_schema.py::test_audit_is_append_only_and_chain_verifies`, `/api/v1/audit/verify` |
| C19 | Browser: strict CSP (no inline script, no `unsafe-eval`), all telemetry and model text rendered as text (no HTML/markdown rendering), URL-scheme allowlist (`http`, `https`) for links | stored XSS | this document | planned |
| C20 | Per-organization quotas on queued and running investigations; ingestion rate limits per key | queue flooding / starvation | ADR-010 | implemented — per-org running cap, queue limit + deferral, per-key ingestion rate limit (`test_queue_and_worker.py`, `test_security.py::test_ingestion_rate_limit`) |
| C21 | Configuration validation rejects unsafe production settings (dev auth, wildcard CORS/origins, debug, non-HTTPS public URL, default secrets, broad connector ranges) | misconfiguration | ADR-011/016 | implemented — `tests/test_config.py`, registry validation (`test_registry_validation_rules`) |
| C22 | Kubernetes integration RBAC limited to events, deployments/replicasets/pods metadata (no Secrets, no exec) | over-privileged reads | ADR-016 | planned |
| C23 | Secret scanning, dependency and image scanning, SBOM in CI | supply chain, leaked secrets | roadmap M9 | planned |

## 7. Claims

**Aegis MAY claim (each backed by a named test):**
* Model output cannot raise the support level above the deterministic level.
* Model output cannot cite nonexistent evidence.
* No remediation runs that a human did not author in advance (catalog) and approve.
* No cross-tenant access on the tested paths.
* Aegis connects only to operator-registered endpoints, never to its own infrastructure or metadata addresses.

**Aegis MUST NOT claim:**
* injection-proof, injection-resistant, or "cannot be fooled" (forged telemetry can mislead the deterministic layer; the honest statement is "never more confident than the data");
* an audit trail tamper-evident against the database owner;
* a two-person rule that holds against the operator or the identity provider;
* complete redaction (redaction is heuristic);
* SSRF-proof;
* isolation from the operator;
* confidentiality from the LLM provider.

## 8. Data outside RLS

Application and worker logs, error traces, metrics labels, backups and the LLM provider's own logs can contain tenant data. Controls: structured logs never include evidence payloads or prompts (IDs only); redaction before any prompt; backups are the operator's responsibility and inherit the operator trust boundary; cloud LLM use is opt-in per organization.
